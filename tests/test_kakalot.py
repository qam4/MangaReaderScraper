"""
Tests for the shared kakalot-family engine (manganelo / manganato / mangakaka),
rewritten (C2) against fresh captured fixtures. The fetch seam
(scraper.parsers.base.fetch_soup / scraper.parsers.base.fetch_soup) is mocked
so no live site is ever touched.
"""

from pathlib import Path
from unittest import mock

import bs4
import pytest

from scraper.exceptions import ChapterDoesntExist, NoSearchResultsFound
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


# ----------------------- all_chapter_ids / chapter_url ----------------------


def test_all_chapter_ids_and_chapter_url_from_map():
    chapters = _soup(NELO / "naruto_chapters.html")
    with mock.patch("scraper.parsers.base.fetch_soup", return_value=chapters):
        parser = ManganeloMangaParser("naruto")
        ids = list(parser.all_chapter_ids())
    assert "700.6" in ids and "700.5" in ids
    # chapter_url resolves from the cached {number: href} map (no second fetch)
    assert parser.chapter_url("700.6") == (
        "https://www.nelomanga.net/manga/naruto/chapter-700-6"
    )
    with pytest.raises(ChapterDoesntExist):
        parser.chapter_url("99999")


def test_all_chapter_ids_empty_page_raises():
    empty = bs4.BeautifulSoup("<html><body/></html>", "lxml")
    from scraper.exceptions import MangaDoesNotExist

    with mock.patch("scraper.parsers.base.fetch_soup", return_value=empty):
        with pytest.raises(MangaDoesNotExist):
            ManganeloMangaParser("nope").all_chapter_ids()


# -------------------------------- page_urls -------------------------------


ONE_PX_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_page_urls_captures_browser_rendered_images():
    # The CDN only serves the live browser, so page_urls drives the browser to
    # capture image bytes (via fetch_rendered_images) and caches them; it
    # returns the urls in document order.
    parser = MangaKakaMangaParser("dragon-ball")
    parser._chapter_urls = {"520.5": "https://x/manga/dragon-ball/chapter-520-5"}
    fake = mock.Mock()
    fake.fetch_rendered_images.return_value = [
        ("https://cdn/0.webp", ONE_PX_PNG),
        ("https://cdn/1.webp", ONE_PX_PNG),
    ]
    with mock.patch.object(parser, "_page_fetcher", return_value=fake):
        pages = parser.page_urls("520.5")
    assert pages == [(1, "https://cdn/0.webp"), (2, "https://cdn/1.webp")]
    # the reader url + container selector + image attr were passed through
    fake.fetch_rendered_images.assert_called_once_with(
        "https://x/manga/dragon-ball/chapter-520-5",
        parser.reader_selector,
        "src",
    )
    # bytes are cached for page_data to serve
    assert parser._image_bytes["https://cdn/0.webp"] == ONE_PX_PNG


def test_page_urls_no_images_raises():
    parser = MangaKakaMangaParser("dragon-ball")
    parser._chapter_urls = {"520.5": "https://x/c"}
    fake = mock.Mock()
    fake.fetch_rendered_images.return_value = []
    with mock.patch.object(parser, "_page_fetcher", return_value=fake):
        with pytest.raises(ChapterDoesntExist):
            parser.page_urls("520.5")


def test_page_data_serves_cached_browser_bytes():
    # page_data does no network -- it serves the bytes captured in page_urls.
    parser = MangaKakaMangaParser("dragon-ball")
    parser._image_bytes = {"https://cdn/0.webp": ONE_PX_PNG}
    num, data, status = parser.page_data((1, "https://cdn/0.webp"))
    assert (num, status) == (1, "success")
    assert data == ONE_PX_PNG


def test_page_data_missing_returns_placeholder():
    parser = MangaKakaMangaParser("dragon-ball")
    parser._image_bytes = {}
    num, data, status = parser.page_data((3, "https://cdn/2.webp"))
    assert (num, status) == (3, "missing")
    assert data  # a placeholder image was generated


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
