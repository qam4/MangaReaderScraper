"""
Tests for the mangak.io (legacy name: mangabuddy) API-backed parser.

The fixtures (tests/test_files/mangabuddy/*.json) are trimmed-but-authentic
slices of real responses captured from mangak.io via the probe, so these tests
exercise the actual parsing logic against the actual API shapes.

Network paths are mocked: the live questions (does curl_cffi reach the API,
does the image CDN need cookies) can only be answered by a live run.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import FetchResult
from scraper.new_types import SearchResult
from scraper.parsers.mangabuddy import (
    Mangabuddy,
    MangabuddyMangaParser,
    MangabuddySearch,
    _build_id_from_html,
    _chapter_map_from_payload,
    _chapter_number_from_name,
    _images_from_chapter_payload,
    _next_data_from_html,
    _parse_search_items,
    _query_from_slug,
)

FIXTURES = Path("tests/test_files/mangabuddy")
SEARCH_JSON = (FIXTURES / "search_naruto.json").read_text(encoding="utf-8")
CHAPTERS_JSON = (FIXTURES / "chapters_naruto.json").read_text(encoding="utf-8")
CHAPTER_PAGE_JSON = (FIXTURES / "chapter_page.json").read_text(encoding="utf-8")
CHAPTER_PAGE_HTML = (FIXTURES / "chapter_page.html").read_text(encoding="utf-8")


def _ok(text):
    return FetchResult(url="http://x", status=200, text=text)


# ============================== pure helpers =============================


def test_chapter_number_from_name_pulls_displayed_number():
    assert _chapter_number_from_name("Chapter 700.5 : Uzumaki Naruto") == "700.5"
    assert (
        _chapter_number_from_name("Vol.72 Chapter 700.1 : Book Of Thunder") == "700.1"
    )
    assert _chapter_number_from_name("Vol.1 Chapter 0 : Naruto Pilot Manga") == "0"
    assert _chapter_number_from_name("Chapter 17") == "17"


def test_chapter_number_from_name_none_for_unnumbered():
    assert _chapter_number_from_name("Notice.110") is None
    assert _chapter_number_from_name("Chapter break") is None
    assert _chapter_number_from_name("Extra : bonus") is None


def test_query_from_slug():
    assert _query_from_slug("dragon-ball-super") == "dragon ball super"
    assert _query_from_slug("naruto") == "naruto"


def test_chapter_map_uses_name_number_not_api_sequence():
    payload = json.loads(CHAPTERS_JSON)
    mapping = _chapter_map_from_payload(payload)
    # the API's chapter_number for "Chapter 700.5" is 748, but we key on the
    # displayed number from the name
    assert mapping["700.5"] == "chapter-700-5-uzumaki-naruto"
    assert mapping["700.1"] == "vol-72-chapter-700-1-book-of-thunder"
    assert mapping["0"] == "vol-1-chapter-0-naruto-pilot-manga"
    assert mapping["1"] == "vol-1-chapter-1-uzumaki-naruto"


def test_images_from_chapter_payload_next_data_shape():
    # the _next/data shape: pageProps at top level
    payload = json.loads(CHAPTER_PAGE_JSON)
    images = _images_from_chapter_payload(payload)
    assert len(images) == 4
    assert images[0].endswith("7358c3b60772.webp")


def test_images_from_chapter_payload_next_data_html_shape():
    # the __NEXT_DATA__ shape: props.pageProps
    payload = _next_data_from_html(CHAPTER_PAGE_HTML)
    images = _images_from_chapter_payload(payload)
    assert len(images) == 4
    assert images[0].endswith("7358c3b60772.webp")


def test_next_data_from_html_parses_embedded_json():
    data = _next_data_from_html(CHAPTER_PAGE_HTML)
    assert data["buildId"] == "OPsvqfupCJTheB3Vjj-jF"
    assert data["props"]["pageProps"]["initialChapter"]["slug"] == (
        "vol-1-chapter-1-uzumaki-naruto"
    )


def test_next_data_from_html_missing_returns_none():
    assert _next_data_from_html("<html><body>no next data</body></html>") is None
    assert _next_data_from_html('<script id="__NEXT_DATA__">not json</script>') is None


def test_build_id_from_html():
    html = (
        '<script id="__NEXT_DATA__">{"buildId":"OPsvqfupCJTheB3Vjj-jF","x":1}</script>'
    )
    assert _build_id_from_html(html) == "OPsvqfupCJTheB3Vjj-jF"
    assert _build_id_from_html("<html>no build id</html>") is None


def test_parse_search_items_real_shape():
    results = _parse_search_items(json.loads(SEARCH_JSON), "mangabuddy", start=1)
    assert results["1"] == SearchResult(
        title="Naruto",
        manga_url="naruto",
        chapters="700.5",  # from latest chapter name, not chapters_count
        source="mangabuddy",
    )
    assert results["2"].manga_url == "naruto-the-seventh-hokage-reborn"


# ============================== search ===================================


def test_search_hits_api_and_parses():
    with mock.patch(
        "scraper.parsers.mangabuddy.CurlCffiFetcher.get", return_value=_ok(SEARCH_JSON)
    ) as get:
        results = MangabuddySearch("naruto").search()
    assert get.call_args[0][0] == "https://api.mangak.io/titles/search?q=naruto"
    assert results["1"].title == "Naruto"


# ============================== chapter list =============================


def test_all_volume_ids_resolves_title_then_lists_chapters():
    parser = MangabuddyMangaParser("naruto")
    with mock.patch(
        "scraper.parsers.mangabuddy.CurlCffiFetcher.get",
        side_effect=[_ok(SEARCH_JSON), _ok(CHAPTERS_JSON)],
    ) as get:
        vols = list(parser.all_volume_ids())

    # 1st call = search (resolve slug->id), 2nd = chapters for that id+cv
    assert get.call_args_list[0][0][0] == "https://api.mangak.io/titles/search?q=naruto"
    assert (
        get.call_args_list[1][0][0]
        == "https://api.mangak.io/titles/VYPXkPYz/chapters?cv=1780511268548"
    )
    # numbers come from names, sorted ascending (0 first, 700.5 last)
    assert vols[0] == "0"
    assert vols[-1] == "700.5"
    assert vols.index("700.1") < vols.index("700.5")


def test_all_volume_ids_unknown_slug_raises():
    parser = MangabuddyMangaParser("does-not-exist")
    with mock.patch(
        "scraper.parsers.mangabuddy.CurlCffiFetcher.get", return_value=_ok(SEARCH_JSON)
    ):
        with pytest.raises(MangaDoesNotExist):
            parser.all_volume_ids()


def test_all_volume_ids_404_raises_manga_does_not_exist():
    parser = MangabuddyMangaParser("naruto")

    def _side_effect(url, headers=None, timeout=30):
        if "search" in url:
            return _ok(SEARCH_JSON)
        return FetchResult(url=url, status=404, text="")

    with mock.patch(
        "scraper.parsers.mangabuddy.CurlCffiFetcher.get", side_effect=_side_effect
    ):
        with pytest.raises(MangaDoesNotExist):
            parser.all_volume_ids()


# ============================== volume url ===============================


def test_volume_url_maps_number_to_slug():
    parser = MangabuddyMangaParser("naruto")
    with mock.patch(
        "scraper.parsers.mangabuddy.CurlCffiFetcher.get",
        side_effect=[_ok(SEARCH_JSON), _ok(CHAPTERS_JSON)],
    ):
        url = parser.volume_url("700.5")
    assert url == "https://mangak.io/naruto/chapter-700-5-uzumaki-naruto"


def test_volume_url_unknown_chapter_raises():
    parser = MangabuddyMangaParser("naruto")
    parser._chapter_slugs = {"1": "vol-1-chapter-1-uzumaki-naruto"}
    with pytest.raises(VolumeDoesntExist):
        parser.volume_url("999")


# ============================== page urls ================================


def test_page_urls_reads_images_from_embedded_next_data():
    # single browser fetch: images come straight from the page's __NEXT_DATA__,
    # no _next/data round-trip needed
    parser = MangabuddyMangaParser("naruto")
    parser._chapter_slugs = {"1": "vol-1-chapter-1-uzumaki-naruto"}

    fake_fetcher = mock.Mock()
    fake_fetcher.get.return_value = FetchResult("u", 200, CHAPTER_PAGE_HTML)

    with mock.patch(
        "scraper.parsers.mangabuddy.BrowserFetcher", return_value=fake_fetcher
    ):
        pages = parser.page_urls("1")

    # only the page itself was fetched; the _next/data fallback was NOT used
    fake_fetcher.get.assert_called_once()
    fake_fetcher.fetch_json_in_page.assert_not_called()
    assert pages[0] == (
        1,
        "https://rx.qvzrd.org/r/p/44a9873d/c7332944/7358c3b60772.webp",
    )
    assert len(pages) == 4


def test_page_urls_falls_back_to_next_data_endpoint():
    # if the page HTML has no embedded images, fall back to _next/data via buildId
    parser = MangabuddyMangaParser("naruto")
    parser._chapter_slugs = {"1": "vol-1-chapter-1-uzumaki-naruto"}
    html = '<script id="__NEXT_DATA__">{"buildId":"BID123","props":{}}</script>'

    fake_fetcher = mock.Mock()
    fake_fetcher.get.return_value = FetchResult("u", 200, html)
    fake_fetcher.fetch_json_in_page.return_value = CHAPTER_PAGE_JSON

    with mock.patch(
        "scraper.parsers.mangabuddy.BrowserFetcher", return_value=fake_fetcher
    ):
        pages = parser.page_urls("1")

    data_url = fake_fetcher.fetch_json_in_page.call_args[0][1]
    assert "/_next/data/BID123/naruto/vol-1-chapter-1-uzumaki-naruto.json" in data_url
    assert len(pages) == 4


def test_page_urls_no_images_anywhere_raises():
    parser = MangabuddyMangaParser("naruto")
    parser._chapter_slugs = {"1": "vol-1-chapter-1-uzumaki-naruto"}
    fake_fetcher = mock.Mock()
    fake_fetcher.get.return_value = FetchResult("u", 200, "<html>no build</html>")
    with mock.patch(
        "scraper.parsers.mangabuddy.BrowserFetcher", return_value=fake_fetcher
    ):
        with pytest.raises(VolumeDoesntExist):
            parser.page_urls("1")


# ============================== site parser ==============================


def test_site_parser_wires_subparsers():
    mb = Mangabuddy("naruto")
    assert isinstance(mb.manga, MangabuddyMangaParser)
    assert mb.base_url == "https://mangak.io"
