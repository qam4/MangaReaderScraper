"""
Tests for the rewritten mangago parser (C2), against fresh captured fixtures in
tests/test_files/mangago/. The browser fetch seam is mocked so no real network
is touched; only the parsing logic is exercised.
"""

from pathlib import Path
from unittest import mock

import pytest

from scraper.exceptions import VolumeDoesntExist
from scraper.parsers.mangago import (
    MangagoMangaParser,
    MangagoSearch,
    _chapter_map_from_html,
    _chapter_number_from_label,
    _image_urls_from_reader_html,
)

FIXTURES = Path("tests/test_files/mangago")
CHAPTERS_HTML = (FIXTURES / "naruto_chapters.html").read_text(encoding="utf-8")
READER_HTML = (FIXTURES / "naruto_reader.html").read_text(encoding="utf-8")
SEARCH_HTML = (FIXTURES / "naruto_search.html").read_text(encoding="utf-8")


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
    mapping = _chapter_map_from_html(CHAPTERS_HTML)
    # numbers parsed from the <b> label, mapped to the reader href
    assert "700.6" in mapping
    assert "700.5" in mapping
    assert mapping["700.6"] == (
        "https://www.mangago.me/read-manga/naruto/mr/v72/c700.6/pg-1/"
    )


def test_image_urls_from_reader_html_selects_page_imgs_only():
    urls = _image_urls_from_reader_html(READER_HTML)
    assert urls  # found page images
    # real page images come from the mangapicgallery CDN, ordered page1, page2..
    assert all("mangapicgallery.com" in u for u in urls)
    # nav/ui images (arrow.jpg, backtotop.png) must be excluded
    assert not any("arrow" in u or "backtotop" in u for u in urls)


# ----------------------------- manga parser ------------------------------


def test_all_volume_ids_parses_chapters_and_caches_urls():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=CHAPTERS_HTML)
        parser = MangagoMangaParser("naruto")
        ids = list(parser.all_volume_ids())
    assert "700.6" in ids and "700.5" in ids
    # volume_url uses the cached map (no second fetch needed)
    assert parser.volume_url("700.6") == (
        "https://www.mangago.me/read-manga/naruto/mr/v72/c700.6/pg-1/"
    )


def test_volume_url_unknown_chapter_raises():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=CHAPTERS_HTML)
        parser = MangagoMangaParser("naruto")
        parser.all_volume_ids()
        with pytest.raises(VolumeDoesntExist):
            parser.volume_url("99999")


def test_page_urls_returns_enumerated_cdn_images():
    with mock.patch("scraper.parsers.mangago.BrowserFetcher") as BF:
        BF.return_value.get.return_value = mock.Mock(text=READER_HTML)
        parser = MangagoMangaParser("naruto")
        # seed the chapter map so volume_url resolves without another fetch
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
        with pytest.raises(VolumeDoesntExist):
            parser.page_urls("1")


# -------------------------------- search ---------------------------------


def test_search_parses_row_1_cards():
    import bs4

    soup = bs4.BeautifulSoup(SEARCH_HTML, "lxml")
    with mock.patch("scraper.parsers.base.fetch_soup", return_value=soup):
        results = MangagoSearch("naruto").search()
    assert results  # non-empty
    first = results["1"]
    assert first.title  # a real title
    assert first.manga_url  # a slug
    assert first.source == "mangago"
