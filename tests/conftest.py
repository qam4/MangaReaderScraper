"""
Pytest fixtures
"""

import logging
from unittest import mock

import pytest
from bs4 import BeautifulSoup

from scraper.manga import Manga, Page, Volume
from scraper.menu import Menu
from tests.helpers import MockedMangaReaderParser, get_bs4_tree, get_images


@pytest.fixture(scope="session", autouse=True)
def mocked_manga_env_var():
    """
    Mock MANGA_DIR env var in manga module to point to /tmp/ dir

    This will be applied to every single test prior to execution
    """
    mock_dir = f"/tmp"
    with mock.patch("scraper.manga.MANGA_DIR", mock_dir) as mocked_dir:
        yield mocked_dir


@pytest.fixture(scope="session", autouse=True)
def mocked_download_settings():
    mocked_settings = mock.MagicMock(
        return_value={"config": {"manga_directory": "/tmp/"}}
    )
    with mock.patch("scraper.download.settings", mocked_settings) as mocked_dir:
        yield mocked_dir


@pytest.fixture(scope="session", autouse=True)
def mocked_manga_env_var_cli():
    mock_dir = f"/tmp"
    with mock.patch("scraper.__main__.MANGA_DIR", mock_dir) as mocked_dir:
        yield mocked_dir


@pytest.fixture
def parser():
    return MockedMangaReaderParser


@pytest.fixture
def search_html() -> BeautifulSoup:
    """
    HTML result after searching for query 'dragonball'
    """
    html_path = "tests/test_files/dragonball_search.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def invalid_search_html() -> BeautifulSoup:
    """
    HTML result after searching for something that has no matches
    """
    html_path = "tests/test_files/no_search_results_found.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def page_html() -> BeautifulSoup:
    """
    Returns the HTML to a specfic manga volume page
    """
    html_path = "tests/test_files/dragonball_bardock_volume_2_page.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def manga_title_page_html() -> BeautifulSoup:
    html_path = f"tests/test_files/dragonball_bardock_page.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def volume_html() -> BeautifulSoup:
    """
    Returns the HTML to a specfic manga volume
    """
    html_path = "tests/test_files/dragonball_bardock_volume_2.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def invalid_volume_html() -> BeautifulSoup:
    """
    Returns the HTML to an invalid manga volume request
    """
    html_path = "tests/test_files/dragonball_bardock_volume_100.html"
    html = get_bs4_tree(html_path)
    return html


@pytest.fixture
def page():
    return Page(1, b"data")


@pytest.fixture
def volume():
    img1, img2 = get_images()
    page_data = [(1, img1), (2, img2)]
    volume = Volume(1, "/Some/path", "/some/path")
    volume.pages = page_data
    return volume


@pytest.fixture
def manga():
    manga = Manga("dragon-ball", "pdf")
    manga.volumes = [1, 2]
    manga.volume[1].pages = [(1, b"here"), (2, b"bye")]
    manga.volume[2].pages = [(1, b"hello"), (2, b"jimmy")]
    return manga


@pytest.fixture
def logger():
    return logging.getLogger("unittest_logger")


@pytest.fixture
def menu():
    choices = "pick A or B for stuff"
    options = {"A": "1", "B": "2"}
    parent = Menu(options, choices)
    choices = (
        "+----+---------------------------------+-----------+--------+\n"
        "|    | Title                           |   Volumes | Type   |\n"
        "|----+---------------------------------+-----------+--------|\n"
        "|  1 | Dragon Ball                     |       520 | Manga  |\n"
        "|  2 | Dragon Ball SD                  |        34 | Manga  |\n"
        "|  3 | Dragon Ball: Episode of Bardock |         3 | Manga  |\n"
        "|  4 | DragonBall Next Gen             |         4 | Manga  |\n"
        "|  5 | Dragon Ball Z - Rebirth of F    |         3 | Manga  |\n"
        "|  6 | Dragon Ball Super               |        54 | Manga  |\n"
        "+----+---------------------------------+-----------+--------+"
    )
    options = {
        "1": "dragon-ball",
        "2": "dragon-ball-sd",
        "3": "dragon-ball-episode-of-bardock",
        "4": "dragonball-next-gen",
        "5": "dragon-ball-z-rebirth-of-f",
        "6": "dragon-ball-super",
    }
    return Menu(options, choices, parent)


@pytest.fixture
def menu_no_choices():
    choices = (
        "+----+---------------------------------+-----------+--------+\n"
        "|    | Title                           |   Volumes | Type   |\n"
        "|----+---------------------------------+-----------+--------|\n"
        "|  1 | Dragon Ball                     |       520 | Manga  |\n"
        "|  2 | Dragon Ball SD                  |        34 | Manga  |\n"
        "|  3 | Dragon Ball: Episode of Bardock |         3 | Manga  |\n"
        "|  4 | DragonBall Next Gen             |         4 | Manga  |\n"
        "|  5 | Dragon Ball Z - Rebirth of F    |         3 | Manga  |\n"
        "|  6 | Dragon Ball Super               |        54 | Manga  |\n"
        "+----+---------------------------------+-----------+--------+"
    )
    options = {
        "1": "dragon-ball",
        "2": "dragon-ball-sd",
        "3": "dragon-ball-episode-of-bardock",
        "4": "dragonball-next-gen",
        "5": "dragon-ball-z-rebirth-of-f",
        "6": "dragon-ball-super",
    }
    parent = Menu(options, choices)
    options = {"A": "1", "B": "2"}
    child = Menu(options, parent=parent)
    return child
