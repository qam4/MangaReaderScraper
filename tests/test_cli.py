import os
import subprocess
import sys
from unittest import mock

import pytest

from scraper.__main__ import cli, cli_entry, get_manga_parser, manga_search
from scraper.exceptions import MangaDoesNotExist, OfflineSeriesNotFound
from scraper.manga import SERIES_FILE
from tests.helpers import MockedSiteParser

PARAMETERS = [
    (
        ["--manga", "dragonball"],
        {
            "filetype": "pdf",
            "manga": "dragonball",
            "output": None,
            "search": None,
            "source": "mangabuddy",
            "chapters": None,
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
        },
    ),
    (
        ["--manga", "dragonball", "--chapters", "1", "2"],
        {
            "filetype": "pdf",
            "manga": "dragonball",
            "output": None,
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1", "2"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
        },
    ),
    (
        ["--manga", "one-piece", "--chapters", "231", "--filetype", "cbz"],
        {
            "filetype": "cbz",
            "manga": "one-piece",
            "output": None,
            "search": None,
            "source": "mangabuddy",
            "chapters": ["231"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "offline": False,
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
            "output": None,
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1-5", "40"],
            "upload": None,
            "override_name": "dragon_kin",
            "remove": False,
            "bundle": None,
            "offline": False,
        },
    ),
    (
        ["--manga", "something", "--chapters", "1-5", "40"],
        {
            "filetype": "pdf",
            "manga": "something",
            "output": None,
            "search": None,
            "source": "mangabuddy",
            "chapters": ["1-5", "40"],
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "output": None,
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "output": None,
            "filetype": "pdf",
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "output": None,
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "output": None,
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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
            "output": None,
            "filetype": "pdf",
            "upload": None,
            "override_name": None,
            "remove": False,
            "bundle": None,
            "offline": False,
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


# --------------------------- --offline (item 4) ---------------------------


def test_offline_bundles_from_disk_without_touching_the_site():
    offline_manga = mock.Mock(name="offline manga")
    with (
        mock.patch(
            "scraper.__main__.download_manga",
            side_effect=AssertionError("offline must not download"),
        ),
        mock.patch(
            "scraper.__main__.get_manga_parser",
            side_effect=AssertionError("offline must not even pick a parser"),
        ),
        mock.patch(
            "scraper.__main__.load_offline_manga", return_value=offline_manga
        ) as load,
        mock.patch("scraper.__main__.bundle") as bundle,
    ):
        cli(
            [
                "--manga", "Dragon", "Ball", "--offline", "--bundle", "10",
                "--chapters", "1-20", "--source", "mangabuddy",
            ]
        )  # fmt: skip

    load.assert_called_once_with(
        "Dragon Ball", "cbz", chapter_ids=["1-20"], directory=None
    )
    bundle.assert_called_once_with(offline_manga, 10, jobs=None)


def test_offline_uses_override_name_as_the_series_folder():
    with (
        mock.patch("scraper.__main__.load_offline_manga") as load,
        mock.patch("scraper.__main__.bundle"),
    ):
        cli(
            [
                "--manga",
                "dragon-ball",
                "-n",
                "Dragon Ball",
                "--offline",
                "--bundle",
                "5",
            ]
        )
    assert load.call_args[0][0] == "Dragon Ball"


@pytest.mark.parametrize(
    "arguments,message",
    [
        (["--manga", "x", "--offline"], "with --bundle"),
        (["--search", "x", "--offline", "--bundle", "5"], "--search"),
        (["--manga", "x", "--offline", "--bundle", "5", "-u", "dropbox"], "--upload"),
        (["--offline", "--bundle", "5"], "--manga"),
    ],
)
def test_offline_rejects_combinations_that_need_the_site(arguments, message):
    with mock.patch("scraper.__main__.load_offline_manga") as load:
        with pytest.raises(IOError, match=message):
            cli(arguments)
    load.assert_not_called()


def test_offline_without_a_record_exits_cleanly(caplog):
    # a clear message and exit 1, not a traceback
    argv = ["manga-scraper", "--manga", "x", "--offline", "--bundle", "5"]
    with (
        mock.patch("sys.argv", argv),
        # cli() reconfigures root logging with force=True, which would remove
        # caplog's capture handler; that's logging setup, not what's tested here
        mock.patch("scraper.__main__.configure_logging"),
        mock.patch(
            "scraper.__main__.load_offline_manga",
            side_effect=OfflineSeriesNotFound("run it once without --offline"),
        ),
    ):
        with pytest.raises(SystemExit) as exit_info:
            cli_entry()
    assert exit_info.value.code == 1
    assert "run it once without --offline" in caplog.text


# ----------------------- import side effects (G8) -------------------------


def test_importing_the_cli_does_not_read_or_create_the_user_ini(tmp_path):
    # In a fresh interpreter with an empty home: importing scraper.__main__
    # used to run settings() at import, which reads ~/.config/mangascraper.ini
    # and creates it when missing. A subprocess, because the module is already
    # imported (and patched) in this one.
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
    result = subprocess.run(
        [sys.executable, "-c", "import scraper.__main__"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert not (home / ".config" / "mangascraper.ini").exists()


# ------------------------ --output (backlog G7) ---------------------------
#
# --output was parsed but read by nothing from 2018 (4b872b9) on, so `-o X`
# silently saved into the ini manga_directory. It is the download folder for
# the run, which --offline must then read from as well.


def test_output_sets_the_download_folder_for_this_run(tmp_path, manga_directory):
    out = tmp_path / "elsewhere"
    with mock.patch("scraper.__main__.get_manga_parser", return_value=MockedSiteParser):
        cli(["--manga", "dragon-ball", "--chapters", "1", "--output", str(out)])
    assert (out / "dragon-ball" / "dragon-ball_chapter_1.pdf").is_file()
    assert (out / "dragon-ball" / SERIES_FILE).is_file()  # the --offline record
    assert not (manga_directory / "dragon-ball").exists()


def test_without_output_downloads_go_to_the_configured_folder(manga_directory):
    with mock.patch("scraper.__main__.get_manga_parser", return_value=MockedSiteParser):
        cli(["--manga", "dragon-ball", "--chapters", "1"])
    assert (manga_directory / "dragon-ball" / "dragon-ball_chapter_1.pdf").is_file()


def test_output_reaches_the_download_after_a_fallback_search(tmp_path):
    out = str(tmp_path / "elsewhere")
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise MangaDoesNotExist("nope")  # the slug lookup fails

    with (
        mock.patch("scraper.__main__.download_manga", side_effect=fake_download),
        mock.patch(
            "scraper.__main__.manga_search", return_value=("Title", "slug", ["2"])
        ),
    ):
        cli(["--manga", "nope", "-o", out])
    assert [c.get("directory") for c in calls] == [out, out]


def test_offline_reads_the_series_from_the_output_folder(tmp_path):
    out = tmp_path / "elsewhere"
    with mock.patch("scraper.__main__.get_manga_parser", return_value=MockedSiteParser):
        cli(["--manga", "dragon-ball", "-q", "1", "-f", "cbz", "-o", str(out)])
    with mock.patch("scraper.__main__.bundle") as bundle:
        cli(["--manga", "dragon-ball", "-q", "1", "--offline", "--bundle", "1",
             "-o", str(out)])  # fmt: skip
    manga = bundle.call_args[0][0]
    assert [c.file_path for c in manga.chapters] == [
        out / "dragon-ball" / "dragon-ball_chapter_1.cbz"
    ]


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
                "output": None,
                "filetype": "pdf",
                "upload": None,
                "override_name": None,
                "remove": False,
                "bundle": None,
                "offline": False,
            }
            assert args == expected


def test_manga_search_honors_preselected_chapters():
    # --search WITH --chapters: the user picks the manga, but the CLI chapter
    # selection is used as-is (no interactive prompt) -- previously it was
    # silently dropped.
    fake_menu = mock.Mock()
    fake_menu.handle_options.return_value = {"title": "Naruto", "manga_url": "naruto"}
    with mock.patch("scraper.__main__.SearchMenu", return_value=fake_menu):
        with mock.patch("scraper.__main__.menu_input") as prompt:
            title, url, chapters = manga_search(
                ["naruto"], MockedSiteParser, preselected=["1-3"]
            )
    assert (title, url, chapters) == ("Naruto", "naruto", ["1-3"])
    prompt.assert_not_called()


def test_manga_search_prompts_when_no_preselection():
    # --search WITHOUT --chapters: still prompts interactively (unchanged).
    fake_menu = mock.Mock()
    fake_menu.handle_options.return_value = {"title": "Naruto", "manga_url": "naruto"}
    with mock.patch("scraper.__main__.SearchMenu", return_value=fake_menu):
        with mock.patch("scraper.__main__.menu_input", return_value="1 2 5") as prompt:
            _title, _url, chapters = manga_search(["naruto"], MockedSiteParser)
    assert chapters == ["1", "2", "5"]
    prompt.assert_called_once()
