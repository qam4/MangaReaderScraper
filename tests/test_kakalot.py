"""
Tests for the shared kakalot-family engine.

manganelo/manganato have no other test files, so these pin the per-site URL
templates and parameters that the engine is built from (the differences that
were previously hard-coded in three near-identical parsers).
"""

from scraper.parsers.mangakaka import MangaKakaMangaParser
from scraper.parsers.manganato import ManganatoMangaParser
from scraper.parsers.manganelo import ManganeloMangaParser


def test_mangakaka_urls_and_params():
    p = MangaKakaMangaParser("dragon_ball_super")
    assert p.volume_url("55") == (
        "https://mangakakalot.gg/chapter/dragon_ball_super/chapter_55"
    )
    assert p._manga_page_url() == "https://mangakakalot.gg/manga/dragon_ball_super"
    assert p.page_img_attr == "src"
    # href like .../chapter_55 -> "55"
    assert p._extract_number("https://x/chapter/dragon_ball_super/chapter_55") == "55"


def test_manganelo_urls_and_params():
    p = ManganeloMangaParser("dragon-ball")
    assert p.volume_url("10") == (
        "https://nelomanga.net/chapter/manga-dragon-ball/chapter-10"
    )
    assert p._manga_page_url() == "https://nelomanga.net/manga/manga-dragon-ball"
    # nelomanga uses lazy-loaded images
    assert p.page_img_attr == "data-src"
    assert p._extract_number("https://x/chapter/manga-dragon-ball/chapter-10") == "10"


def test_manganato_urls_and_params():
    p = ManganatoMangaParser("dragon-ball")
    assert p.volume_url("10") == "https://natomanga.com/manga-dragon-ball/chapter-10"
    assert p._manga_page_url() == "https://natomanga.com/manga-dragon-ball"
    assert p.page_img_attr == "src"
    assert p._extract_number("https://x/manga-dragon-ball/chapter-10") == "10"


def test_extract_number_handles_decimal_chapters():
    p = MangaKakaMangaParser("x")
    assert p._extract_number("https://x/chapter/x/chapter_520.5") == "520.5"
