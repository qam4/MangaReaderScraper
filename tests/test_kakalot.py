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
    html = (NELO / "naruto_chapters.html").read_text(encoding="utf-8")
    parser = ManganeloMangaParser("naruto")
    fake = mock.Mock()
    fake.get_after_scroll.return_value = mock.Mock(text=html)
    with mock.patch.object(parser, "_page_fetcher", return_value=fake):
        ids = list(parser.all_chapter_ids())
    assert "700.6" in ids and "700.5" in ids
    # chapter_url resolves from the cached {number: href} map (no second fetch)
    assert parser.chapter_url("700.6") == (
        "https://www.nelomanga.net/manga/naruto/chapter-700-6"
    )
    with pytest.raises(ChapterDoesntExist):
        parser.chapter_url("99999")
    # the series page was scroll-loaded against the chapter-list container
    call = fake.get_after_scroll.call_args
    assert call.args[0] == "https://nelomanga.net/manga/naruto"
    assert call.kwargs["scroll_selector"] == "div.chapter-list"


def test_all_chapter_ids_empty_page_raises():
    from scraper.exceptions import MangaDoesNotExist

    parser = ManganeloMangaParser("nope")
    fake = mock.Mock()
    fake.get_after_scroll.return_value = mock.Mock(text="<html><body/></html>")
    with mock.patch.object(parser, "_page_fetcher", return_value=fake):
        with pytest.raises(MangaDoesNotExist):
            parser.all_chapter_ids()


# -------------------------------- page_urls -------------------------------


def test_page_urls_not_supported_fails_fast():
    # Page-image download is blocked for this family: the reader is behind an
    # interactive Cloudflare Turnstile and a browser-only image CDN. page_urls
    # must fail fast (no browser, no hang) with a message that explains why.
    parser = MangaKakaMangaParser("dragon-ball")
    parser._chapter_urls = {"520.5": "https://x/manga/dragon-ball/chapter-520-5"}
    with pytest.raises(ChapterDoesntExist) as excinfo:
        parser.page_urls("520.5")
    message = str(excinfo.value).lower()
    assert "turnstile" in message
    assert "not" in message and "supported" in message


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
