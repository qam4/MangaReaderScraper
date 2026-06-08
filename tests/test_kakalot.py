"""
Tests for the shared kakalot-family engine (manganelo / manganato / mangakaka),
rewritten (C2) against fresh captured fixtures. The fetch seam
(scraper.parsers.kakalot.fetch_soup / scraper.parsers.base.fetch_soup) is mocked
so no live site is ever touched.
"""

from pathlib import Path
from unittest import mock

import bs4
import pytest

from scraper.exceptions import NoSearchResultsFound, VolumeDoesntExist
from scraper.parsers.kakalot import (
    _chapter_map_from_soup,
    _chapter_number_from_text,
)
from scraper.parsers.mangakaka import MangaKakaMangaParser, MangaKakaSearch
from scraper.parsers.manganato import ManganatoMangaParser
from scraper.parsers.manganelo import ManganeloMangaParser, ManganeloSearch

NELO = Path("tests/test_files/manganelo")
KAKA = Path("tests/test_files/mangakaka")


def _soup(path: Path) -> bs4.BeautifulSoup:
    return bs4.BeautifulSoup(path.read_text(encoding="utf-8"), "lxml")


# ------------------------------ pure helpers ------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Chapter 700.6", "700.6"),
        ("Chapter 12", "12"),
        ("Chapter 0", "0"),
        ("Vol.1 Chapter 5.5", "5.5"),
        ("Read now", None),
    ],
)
def test_chapter_number_from_text(label, expected):
    assert _chapter_number_from_text(label) == expected


def test_chapter_map_from_soup_maps_number_to_full_href():
    mapping = _chapter_map_from_soup(_soup(NELO / "naruto_chapters.html"))
    assert "700.6" in mapping
    assert mapping["700.6"] == ("https://www.nelomanga.net/manga/naruto/chapter-700-6")


# -------------------------- per-site parameters ---------------------------


def test_site_params():
    assert ManganeloMangaParser("x").page_img_attr == "src"
    assert ManganatoMangaParser("x").page_img_attr == "src"
    assert MangaKakaMangaParser("x").page_img_attr == "src"
    assert ManganeloMangaParser("naruto")._manga_page_url() == (
        "https://nelomanga.net/manga/naruto"
    )
    assert ManganatoMangaParser("naruto")._manga_page_url() == (
        "https://natomanga.com/manga/naruto"
    )
    assert MangaKakaMangaParser("dragon-ball")._manga_page_url() == (
        "https://mangakakalot.gg/manga/dragon-ball"
    )


# ----------------------- all_volume_ids / volume_url ----------------------


def test_all_volume_ids_and_volume_url_from_map():
    chapters = _soup(NELO / "naruto_chapters.html")
    with mock.patch("scraper.parsers.kakalot.fetch_soup", return_value=chapters):
        parser = ManganeloMangaParser("naruto")
        ids = list(parser.all_volume_ids())
    assert "700.6" in ids and "700.5" in ids
    # volume_url resolves from the cached {number: href} map (no second fetch)
    assert parser.volume_url("700.6") == (
        "https://www.nelomanga.net/manga/naruto/chapter-700-6"
    )
    with pytest.raises(VolumeDoesntExist):
        parser.volume_url("99999")


def test_all_volume_ids_empty_page_raises():
    empty = bs4.BeautifulSoup("<html><body/></html>", "lxml")
    from scraper.exceptions import MangaDoesNotExist

    with mock.patch("scraper.parsers.kakalot.fetch_soup", return_value=empty):
        with pytest.raises(MangaDoesNotExist):
            ManganeloMangaParser("nope").all_volume_ids()


# -------------------------------- page_urls -------------------------------


def test_page_urls_reads_container_images_nelo():
    chapters = _soup(NELO / "naruto_chapters.html")
    reader = _soup(NELO / "naruto_reader.html")
    with mock.patch(
        "scraper.parsers.kakalot.fetch_soup", side_effect=[chapters, reader]
    ):
        parser = ManganeloMangaParser("naruto")
        parser.all_volume_ids()  # populates the chapter map (first fetch_soup)
        pages = parser.page_urls("700.6")  # reader page (second fetch_soup)
    assert pages[0][0] == 1
    assert pages[0][1] == "https://img-r1.2xstorage.com/naruto/700.6/0.webp"
    assert [n for n, _ in pages] == list(range(1, len(pages) + 1))


def test_page_urls_reads_container_images_kaka_src():
    reader = _soup(KAKA / "dragonball_reader.html")
    with mock.patch("scraper.parsers.kakalot.fetch_soup", return_value=reader):
        parser = MangaKakaMangaParser("dragon-ball")
        parser._chapter_urls = {"520.5": "https://x/manga/dragon-ball/chapter-520-5"}
        pages = parser.page_urls("520.5")
    assert pages[0][1] == "https://img-r1.2xstorage.com/dragon-ball/520.5/0.webp"


# --------------------------------- search ---------------------------------


def test_search_parses_story_items():
    soup = _soup(NELO / "naruto_search.html")
    with mock.patch("scraper.parsers.base.fetch_soup", return_value=soup):
        results = ManganeloSearch("naruto").search()
    assert results
    first = results["1"]
    assert first.title
    assert first.manga_url
    assert first.source == "manganelo"


def test_search_no_results_raises():
    empty = bs4.BeautifulSoup("<html><body/></html>", "lxml")
    with mock.patch("scraper.parsers.base.fetch_soup", return_value=empty):
        with pytest.raises(NoSearchResultsFound):
            MangaKakaSearch("zzzz").search()
