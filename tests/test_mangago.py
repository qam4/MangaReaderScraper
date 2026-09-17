"""
Tests for the rewritten mangago parser (C2), against fresh captured fixtures in
tests/test_files/mangago/. The browser fetch seam is mocked so no real network
is touched; only the parsing logic is exercised.
"""

from pathlib import Path
from unittest import mock

import bs4
import pytest

from scraper.exceptions import ChapterDoesntExist
from scraper.parsers.mangago import (
    MangagoMangaParser,
    MangagoSearch,
    _authors_from_soup,
    _chapter_map_from_soup,
    _chapter_number_from_label,
    _image_urls_from_soup,
)

FIXTURES = Path("tests/test_files/mangago")
CHAPTERS_HTML = (FIXTURES / "naruto_chapters.html").read_text(encoding="utf-8")
READER_HTML = (FIXTURES / "naruto_reader.html").read_text(encoding="utf-8")
SEARCH_HTML = (FIXTURES / "naruto_search.html").read_text(encoding="utf-8")


def _soup(html):
    return bs4.BeautifulSoup(html, "lxml")


# ------------------------------ pure helpers ------------------------------


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Vol.72 Ch.700.6", "700.6"),
        ("Ch.700.5", "700.5"),
        ("Chapter 12", "12"),
        ("Vol.1 Ch.0", "0"),
        ("Vol.72 Special Gaiden", None),  # no chapter number -> skipped
    ],
)
def test_chapter_number_from_label(label, expected):
    assert _chapter_number_from_label(label) == expected


def test_chapter_map_from_html_uses_b_label_and_href():
    mapping = _chapter_map_from_soup(_soup(CHAPTERS_HTML))
    # numbers parsed from the <b> label, mapped to the reader href
    assert "700.6" in mapping
    assert "700.5" in mapping
    assert mapping["700.6"] == (
        "https://www.mangago.me/read-manga/naruto/mr/v72/c700.6/pg-1/"
    )


def test_image_urls_from_reader_html_selects_page_imgs_only():
    urls = _image_urls_from_soup(_soup(READER_HTML))
    assert urls  # found page images
    # real page images come from the mangapicgallery CDN, ordered page1, page2..
    assert all("mangapicgallery.com" in u for u in urls)
    # nav/ui images (arrow.jpg, backtotop.png) must be excluded
    assert not any("arrow" in u or "backtotop" in u for u in urls)


# ----------------------------- manga parser ------------------------------


def test_all_chapter_ids_parses_chapters_and_caches_urls():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=CHAPTERS_HTML)
        parser = MangagoMangaParser("naruto")
        ids = list(parser.all_chapter_ids())
    assert "700.6" in ids and "700.5" in ids
    # chapter_url uses the cached map (no second fetch needed)
    assert parser.chapter_url("700.6") == (
        "https://www.mangago.me/read-manga/naruto/mr/v72/c700.6/pg-1/"
    )


def test_chapter_url_unknown_chapter_raises():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=CHAPTERS_HTML)
        parser = MangagoMangaParser("naruto")
        parser.all_chapter_ids()
        with pytest.raises(ChapterDoesntExist):
            parser.chapter_url("99999")


def test_page_urls_returns_enumerated_cdn_images():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=READER_HTML)
        parser = MangagoMangaParser("naruto")
        # seed the chapter map so chapter_url resolves without another fetch
        parser._chapter_urls = {"700.6": "https://www.mangago.me/x/mr/v72/c700.6/pg-1/"}
        pages = parser.page_urls("700.6")
    assert pages[0][0] == 1
    assert "mangapicgallery.com" in pages[0][1]
    assert [n for n, _ in pages] == list(range(1, len(pages) + 1))


def test_page_urls_raises_when_no_images():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text="<html><body/></html>")
        parser = MangagoMangaParser("naruto")
        parser._chapter_urls = {"1": "https://www.mangago.me/x/mr/v1/c1/pg-1/"}
        with pytest.raises(ChapterDoesntExist):
            parser.page_urls("1")


# -------------------------------- author ---------------------------------


def test_authors_from_soup_reads_the_author_field():
    assert _authors_from_soup(_soup(CHAPTERS_HTML)) == "Kishimoto Masashi"


def test_authors_from_soup_joins_and_dedupes_multiple():
    html = """<table><tr><td><label>Author: </label>
        <a href="/r/l_search/?name=a">Alice</a>
        <a href="/r/l_search/?name=b">Bob</a>
        <a href="/r/l_search/?name=a">Alice</a>
        1999 released.</td></tr></table>"""
    assert _authors_from_soup(_soup(html)) == "Alice, Bob"


def test_authors_from_soup_ignores_other_labelled_fields():
    # only the Author: cell counts -- not Status:, Genre(s):, Alternative:
    html = """<table>
        <tr><td><label>Status: </label><a href="/x">Completed</a></td></tr>
        <tr><td><label>Genre(s): </label><a href="/g/action">Action</a></td></tr>
        </table>"""
    assert _authors_from_soup(_soup(html)) is None


def test_authors_from_soup_none_when_field_empty():
    html = "<table><tr><td><label>Author: </label>unknown</td></tr></table>"
    assert _authors_from_soup(_soup(html)) is None


def test_author_shares_the_cached_series_page_with_chapter_list():
    """The author must not cost a second browser fetch.

    MangaBuilder calls author() before all_chapter_ids(), and both read the
    series page, so one fetch has to serve both.
    """
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=CHAPTERS_HTML)
        parser = MangagoMangaParser("naruto")
        author = parser.author()  # runs first, as in the real pipeline
        ids = list(parser.all_chapter_ids())

    assert author == "Kishimoto Masashi"
    assert "700.6" in ids  # chapter list still parsed correctly
    BF.return_value.get.assert_called_once()  # ONE series-page fetch total


def test_author_returns_none_on_fetch_failure():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.side_effect = Exception("browser down")
        assert MangagoMangaParser("naruto").author() is None


# -------------------------------- search ---------------------------------


def test_search_parses_box_cards_with_latest_chapter():
    import bs4

    soup = bs4.BeautifulSoup(SEARCH_HTML, "lxml")
    with mock.patch("scraper.parsers.base.fetch_soup", return_value=soup):
        results = MangagoSearch("naruto").search()
    assert results  # non-empty
    first = results["1"]
    assert first.title == "Naruto"
    assert first.manga_url == "naruto"
    assert first.source == "mangago"
    # latest chapter (the previously-blank "Latest Chapter" column) is now filled
    # from the a.chico "Latest Chapters" link ("Vol.72 Ch.700.6" -> "700.6")
    assert first.latest_chapter == "700.6"
