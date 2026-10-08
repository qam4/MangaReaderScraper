"""
Tests for the MangaFire parser (the site as rebuilt in 2026).

The fixtures in tests/test_files/mangafire/ are trimmed but otherwise
unedited responses from MangaFire's JSON API, captured with the probe in
October 2026 (scraper/probe.py --multi --search naruto):

  search_titles.json    /api/titles?keyword=naruto&...&page=1&limit=30
  title_details.json    /api/titles/92kk8
  chapters_page_1.json  /api/titles/92kk8/chapters?...&page=1&limit=20
  chapter_pages.json    /api/chapters/1326884

The browser is never started: BrowserFetcher's capture calls are mocked, and
what is tested is which page and request the parser asks for and how it reads
the replies.
"""

import io
import json
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from scraper.exceptions import ChapterDoesntExist, MangaDoesNotExist
from scraper.fetchers import CaptureTriggerFailed
from scraper.new_types import SearchResult
from scraper.parsers.mangafire import (
    _NEXT_PAGE_JS,
    _SEARCH_BOX,
    Mangafire,
    MangafireMangaParser,
    MangafireSearch,
    _authors_from_title_payload,
    _chapter_map_from_pages,
    _chapter_number,
    _has_next_page,
    _is_search_results_request,
    _page_urls_from_payload,
    _parse_search_payload,
    _series_path,
)

FIXTURES = Path("tests/test_files/mangafire")
SEARCH = (FIXTURES / "search_titles.json").read_text(encoding="utf-8")
TITLE = (FIXTURES / "title_details.json").read_text(encoding="utf-8")
CHAPTERS_1 = (FIXTURES / "chapters_page_1.json").read_text(encoding="utf-8")
PAGES = (FIXTURES / "chapter_pages.json").read_text(encoding="utf-8")

# request urls exactly as the site made them (from the captures' ajax logs)
RESULTS_URL = (
    "https://mangafire.to/api/titles?keyword=naruto&content_rating%5B%5D=safe"
    "&content_rating%5B%5D=suggestive&order%5Brelevance%5D=desc&page=1&limit=30"
    "&vrf=8sK3xtqdFZdOu6WNqS1bZ0shnUDqyRXMnh4NlZ7aYCPUhmAbm1C1qPzeL_OIIf0obIggCZIH"
)
SUGGEST_URL = (
    "https://mangafire.to/api/titles?keyword=naruto&content_rating%5B%5D=safe"
    "&content_rating%5B%5D=suggestive&genres_ex%5B%5D=7&limit=5&vrf=8sK3xtqd"
)
HOT_URL = (
    "https://mangafire.to/api/titles?content_rating%5B%5D=safe&order%5B"
    "chapter_updated_at%5D=desc&hot=1&page=1&limit=30&vrf=8sK3xtqdFZdOu6WN"
)
DETAILS_URL = "https://mangafire.to/api/titles/92kk8?vrf=8sK3xtqdFdtJkF1mjQ"
VOLUMES_URL = "https://mangafire.to/api/titles/92kk8/volumes?vrf=8sK3xtqdFdtJkF1mjWoS"
CHAPTERS_URL = (
    "https://mangafire.to/api/titles/92kk8/chapters?language=en&sort=number"
    "&order=desc&page=1&limit=20&vrf=8sK3xtqdFdtJkF1mjWqnsSN-Z79bNdH_ug8holUtg9Be"
)
PAGES_URL = "https://mangafire.to/api/chapters/1326884?vrf=8vPRXa1JjvVTxq8m42_Kp0s"


def _page_2(numbers_and_ids, has_next=False):
    """A later chapter-list page, in the captured page-1 shape."""
    return json.dumps(
        {
            "items": [
                {"id": cid, "number": n, "name": "", "language": "en"}
                for n, cid in numbers_and_ids
            ],
            "meta": {"page": 2, "lastPage": 2, "hasNext": has_next},
        }
    )


def _parser_with_chapters(*pages):
    parser = MangafireMangaParser("92kk8-naruto")
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr_pages",
        return_value=list(pages),
    ) as capture:
        ids = list(parser.all_chapter_ids())
    return parser, ids, capture


# ============================== small helpers ============================


@pytest.mark.parametrize(
    "value,expected",
    [(700, "700"), (700.0, "700"), (700.5, "700.5"), ("10.1", "10.1"), (None, None)],
)
def test_chapter_number_formats_api_numbers_as_chapter_ids(value, expected):
    assert _chapter_number(value) == expected


@pytest.mark.parametrize(
    "manga_url",
    [
        "92kk8-naruto",
        "/title/92kk8-naruto",
        "https://mangafire.to/title/92kk8-naruto",
        "https://mangafire.to/title/92kk8-naruto/chapter/1326884",
    ],
)
def test_series_path_accepts_the_slug_or_a_pasted_url(manga_url):
    assert _series_path(manga_url) == "92kk8-naruto"


def test_series_url_and_wiring():
    site = Mangafire("92kk8-naruto")
    assert site.base_url == "https://mangafire.to"
    assert isinstance(site.manga, MangafireMangaParser)
    assert site.manga.series_url == "https://mangafire.to/title/92kk8-naruto"


# ================================ search =================================


def test_parse_search_payload_reads_the_real_response():
    results = _parse_search_payload(json.loads(SEARCH), start=1)
    assert len(results) == 5
    assert results["1"] == SearchResult(
        title="Naruto",
        manga_url="92kk8-naruto",
        latest_chapter="700",
        source="mangafire",
    )


def test_search_results_request_is_the_submitted_search_only():
    assert _is_search_results_request(RESULTS_URL)
    assert not _is_search_results_request(SUGGEST_URL)  # the as-you-type box
    assert not _is_search_results_request(HOT_URL)  # the home page's own list


def test_search_types_the_query_on_the_home_page_and_parses_the_reply():
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        return_value=(SEARCH, {}),
    ) as capture:
        results = MangafireSearch("naruto").search()
    url, predicate = capture.call_args[0][:2]
    assert url == "https://mangafire.to/home"
    assert capture.call_args[1]["type_into"] == (_SEARCH_BOX, "naruto")
    assert predicate(RESULTS_URL) and not predicate(SUGGEST_URL)
    assert results["1"]["manga_url"] == "92kk8-naruto"


def test_search_reports_a_missing_search_box_instead_of_raising(caplog):
    failure = CaptureTriggerFailed(
        "no element matching 'input' to type into at https://mangafire.to/home "
        "(title 'Just a moment...'), which looks like a Cloudflare check"
    )
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr", side_effect=failure
    ):
        with caplog.at_level("ERROR"):
            results = MangafireSearch("naruto").search()
    assert results == {}
    assert "search box" in caplog.text
    assert "Cloudflare" in caplog.text


def test_search_reports_a_timeout_instead_of_raising(caplog):
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        side_effect=TimeoutError(),
    ):
        with caplog.at_level("ERROR"):
            results = MangafireSearch("naruto").search()
    assert results == {}
    assert "timed out" in caplog.text


# ============================== chapter list =============================


@pytest.mark.parametrize(
    "number,expected", [(700.5, "700.5"), (700.0, "700"), ("700.10", "700.10")]
)
def test_chapter_number_keeps_decimal_and_string_ids(number, expected):
    assert _chapter_number(number) == expected


def test_chapter_map_from_the_real_first_page():
    chapters = _chapter_map_from_pages([CHAPTERS_1])
    assert len(chapters) == 20
    assert chapters["700"] == "1326884"
    assert chapters["699"] == "1326872"
    assert "681" in chapters


def test_chapter_map_keeps_the_first_upload_of_a_repeated_number():
    chapters = _chapter_map_from_pages([_page_2([(5, 105), (5, 205), (4.5, 145)])])
    assert chapters == {"5": "105", "4.5": "145"}


def test_has_next_page_reads_the_meta():
    assert _has_next_page(CHAPTERS_1) is True  # page 1 of 36
    assert _has_next_page(_page_2([(1, 1)])) is False


def test_all_chapter_ids_pages_through_the_series_page():
    page_2 = _page_2([(2, 902), (1, 901)])
    parser, ids, capture = _parser_with_chapters(CHAPTERS_1, page_2)

    url, predicate, next_js, has_next = capture.call_args[0][:4]
    assert url == "https://mangafire.to/title/92kk8-naruto"
    assert predicate(CHAPTERS_URL)
    assert not predicate(DETAILS_URL) and not predicate(VOLUMES_URL)
    assert next_js == _NEXT_PAGE_JS
    assert has_next(CHAPTERS_1) is True

    assert ids[:2] == ["1", "2"]  # ascending, across pages
    assert ids[-1] == "700"
    assert len(ids) == 22


def test_chapter_url_uses_the_api_chapter_id():
    parser, _, _ = _parser_with_chapters(CHAPTERS_1)
    assert parser.chapter_url("700") == (
        "https://mangafire.to/title/92kk8-naruto/chapter/1326884"
    )
    with pytest.raises(ChapterDoesntExist):
        parser.chapter_url("1")


def test_all_chapter_ids_timeout_is_manga_does_not_exist():
    parser = MangafireMangaParser("92kk8-naruto")
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr_pages",
        side_effect=TimeoutError(),
    ):
        with pytest.raises(MangaDoesNotExist):
            parser.all_chapter_ids()


def test_all_chapter_ids_refuses_a_partial_list(caplog):
    # the API said there were more pages but the next button wasn't there: a
    # partial list would shift every later volume, so it is an error
    parser = MangafireMangaParser("92kk8-naruto")
    failure = CaptureTriggerFailed("next-page script found nothing to click")
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr_pages",
        side_effect=failure,
    ):
        with pytest.raises(MangaDoesNotExist, match="next-page"):
            parser.all_chapter_ids()


def test_next_page_script_targets_the_chapter_pager_and_reports_absence():
    assert "Next page" in _NEXT_PAGE_JS
    assert "title-detail__chapters-pager" in _NEXT_PAGE_JS
    assert "return false" in _NEXT_PAGE_JS


# ============================== page urls ================================


def test_page_urls_from_the_real_payload():
    urls = _page_urls_from_payload(json.loads(PAGES))
    assert [n for n, _ in urls] == [1, 2, 3]
    assert urls[0][1].startswith("https://k99.mfcdn3.xyz/mf/")


def test_page_urls_opens_the_reader_and_catches_its_chapter_request():
    parser, _, _ = _parser_with_chapters(CHAPTERS_1)
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        return_value=(PAGES, {"cf": "cookie"}),
    ) as capture:
        urls = parser.page_urls("700")
    url, predicate = capture.call_args[0][:2]
    assert url == "https://mangafire.to/title/92kk8-naruto/chapter/1326884"
    assert predicate(PAGES_URL)
    assert not predicate("https://mangafire.to/api/chapters/1326872?vrf=x")
    assert len(urls) == 3
    assert parser.cookies == {"cf": "cookie"}  # reused for the image downloads


def test_page_urls_timeout_is_chapter_does_not_exist():
    parser, _, _ = _parser_with_chapters(CHAPTERS_1)
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        side_effect=TimeoutError(),
    ):
        with pytest.raises(ChapterDoesntExist):
            parser.page_urls("700")


def _jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (20, 30), (200, 10, 10)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "downloaded,status",
    [("jpeg", "success"), (None, "missing"), (b"not an image", "corrupted")],
)
def test_page_data_downloads_and_checks_the_image(downloaded, status):
    content = _jpeg() if downloaded == "jpeg" else downloaded
    parser = MangafireMangaParser("92kk8-naruto")
    with mock.patch(
        "scraper.parsers.mangafire.download_image", return_value=content
    ) as download:
        number, data, result = parser.page_data((3, "https://k99.mfcdn3.xyz/p.jpg"))
    assert (number, result) == (3, status)
    assert download.call_args[1]["headers"]["Referer"] == "https://mangafire.to/"
    if status == "success":
        assert data == content


# ================================ author =================================


def test_authors_from_the_real_title_payload():
    assert _authors_from_title_payload(json.loads(TITLE)) == "Kishimoto Masashi"


def test_authors_are_joined_and_deduped():
    payload = {
        "data": {"authors": [{"title": "Alice"}, {"title": "Bob"}, {"title": "Alice"}]}
    }
    assert _authors_from_title_payload(payload) == "Alice, Bob"
    assert _authors_from_title_payload({"data": {"authors": []}}) is None


def test_author_catches_the_series_details_request():
    parser = MangafireMangaParser("92kk8-naruto")
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        return_value=(TITLE, {}),
    ) as capture:
        author = parser.author()
    url, predicate = capture.call_args[0][:2]
    assert url == "https://mangafire.to/title/92kk8-naruto"
    assert predicate(DETAILS_URL)
    assert not predicate(CHAPTERS_URL) and not predicate(VOLUMES_URL)
    assert author == "Kishimoto Masashi"


def test_author_is_none_when_the_lookup_fails():
    parser = MangafireMangaParser("92kk8-naruto")
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        side_effect=TimeoutError(),
    ):
        assert parser.author() is None
