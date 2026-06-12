from unittest import mock

import pytest

from scraper.__main__ import cli, get_manga_parser
from scraper.exceptions import MangaDoesNotExist
from tests.helpers import MockedSiteParser

PARAMETERS = [
    (
        ["--manga", "dragonball"],
        {
            "filetype": "pdf",
            "manga": "dragonball",
            "output": "/tmp",
            "search": None,
            "source": "mangabuddy",
            "chapters": None,
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--manga", "dragonball", "--chapters", "1", "2"],
        {
            "filetype": "pdf",
            "manga": "dragonball",
            "output": "/tmp",
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1", "2"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--manga", "one-piece", "--chapters", "231", "--filetype", "cbz"],
        {
            "filetype": "cbz",
            "manga": "one-piece",
            "output": "/tmp",
            "search": None,
            "source": "mangabuddy",
            "chapters": ["231"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--manga", "something", "--output", "/home/me/Downloads"],
        {
            "filetype": "pdf",
            "manga": "something",
            "output": "/home/me/Downloads",
            "search": None,
            "source": "mangabuddy",
            "chapters": None,
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        [
            "--manga",
            "something",
            "--chapters",
            "1-5",
            "40",
            "--override_name",
            "dragon_kin",
        ],
        {
            "filetype": "pdf",
            "manga": "something",
            "output": "/tmp",
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1-5", "40"],
            "upload": None,
            "override_name": "dragon_kin",
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--manga", "something", "--chapters", "1-5", "40"],
        {
            "filetype": "pdf",
            "manga": "something",
            "output": "/tmp",
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1-5", "40"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
]

SEARCH_PARAMETERS = [
    (
        ["--search", "dragon", "ball"],
        ["1", "5"],
        {
            "manga": "dragon-ball-episode-of-bardock",
            "search": ["dragon", "ball"],
            "source": "mangabuddy",
            "chapters": ["5"],
            "output": "/tmp",
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--search", "dragonball"],
        ["6", "8 9"],
        {
            "manga": "dragon-ball-super",
            "search": ["dragonball"],
            "source": "mangabuddy",
            "chapters": ["8", "9"],
            "output": "/tmp",
            "filetype": "pdf",
            "override_name": None,
            "remove": False,
            "bundle": None,
            "upload": None,
        },
    ),
    (
        ["--search", "dragonball"],
        ["2", "6-10"],
        {
            "manga": "dragon-ball-sd",
            "search": ["dragonball"],
            "source": "mangabuddy",
            "chapters": ["6-10"],
            "output": "/tmp",
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--search", "dragonball"],
        ["2", ""],
        {
            "manga": "dragon-ball-sd",
            "search": ["dragonball"],
            "source": "mangabuddy",
            "chapters": None,
            "output": "/tmp",
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
    (
        ["--search", "dragonball"],
        ["2", "6-10 12"],
        {
            "manga": "dragon-ball-sd",
            "search": ["dragonball"],
            "source": "mangabuddy",
            "chapters": ["6-10", "12"],
            "output": "/tmp",
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
        },
    ),
]


def test_ioerror_remove_upload_args():
    """
    Ensure --remove causes error if --upload arg not present
    """
    with pytest.raises(IOError):
        cli(["--search", "x", "--remove"])


@pytest.mark.parametrize("arguments,expected", PARAMETERS)
@mock.patch("scraper.__main__.download_manga", mock.Mock(return_value=1))
def test_download_via_cli(arguments, expected):
    args = cli(arguments)
    args.pop("log_level", None)  # routing test; log level is asserted elsewhere
    args.pop("jobs", None)  # routing config; pool size asserted in test_utils
    assert args == expected


def test_get_invalid_manga_parser():
    with pytest.raises(ValueError):
        get_manga_parser("nothing")


@mock.patch("scraper.__main__.download_manga", mock.Mock(return_value=1))
def test_log_level_arg_sets_level(monkeypatch):
    import logging

    from scraper.utils import LOG_LEVEL_ENV

    # --log-level applies the chosen level in-process. Workers share this process
    # (ThreadPool), so they inherit it -- no env propagation to assert anymore.
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    cli(["--manga", "dragonball", "--log-level", "DEBUG"])
    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.parametrize("arguments,inputs,expected", SEARCH_PARAMETERS)
@mock.patch("scraper.__main__.download_manga", mock.Mock(return_value=1))
def test_search_via_cli(arguments, inputs, expected, monkeypatch):
    with mock.patch("scraper.__main__.get_manga_parser", return_value=MockedSiteParser):
        gen = (x for x in inputs)
        monkeypatch.setattr("builtins.input", lambda x: next(gen))
        args = cli(arguments)
        args.pop("log_level", None)  # routing test; log level asserted elsewhere
        args.pop("jobs", None)  # routing config; pool size asserted in test_utils
        assert args == expected


def test_search_if_failed_manga_match(monkeypatch):
    def fake_downloader(*args, **kwargs):
        """
        Will raise an error, which should trigger the manga_search
        function and recall this function with the parameters parsed
        from mocked manga_search ("search_activated").

        This will help confirm whether the manga_search function was
        called upon a MangaDoesNotExist error.
        """
        if "search activated" in args or "search activated" in kwargs["manga_url"]:
            return True
        raise MangaDoesNotExist("manga_url")

    with mock.patch("scraper.__main__.download_manga", fake_downloader):
        with mock.patch("scraper.__main__.manga_search") as mocked_func:
            # mock manga_search to return values that signifies it was triggered
            mocked_func.return_value = ("manga title", "search activated", "2")
            args = cli(["--manga", "dragonballzz"])
            args.pop("log_level", None)  # routing test; log level asserted elsewhere
            args.pop("jobs", None)  # routing config; pool size asserted in test_utils
            expected = {
                "manga": "search activated",
                "search": ["dragonballzz"],
                "source": "mangabuddy",
                "chapters": ["2"],
                "output": "/tmp",
                "filetype": "pdf",
                "upload": None,
                "override_name": None,
                "remove": False,
                "bundle": None,
            }
            assert args == expected
