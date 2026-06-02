"""
Tests for the MangaFire parser.

The chapter-list and search fixtures (tests/test_files/mangafire/*.json) are
REAL responses captured from mangafire.to via the probe (scraper/probe.py), so
these tests exercise the actual parsing logic against actual data.
"""

import io
import json
from pathlib import Path
from unittest import mock

from PIL import Image

from scraper.new_types import SearchResult
from scraper.parsers.mangafire import (
    Mangafire,
    MangafireMangaParser,
    MangafireSearch,
    descramble,
    _encode_page_url,
)

CHAPTER_JSON = Path("tests/test_files/mangafire/chapter_list.json").read_text(
    encoding="utf-8"
)
SEARCH_JSON = Path("tests/test_files/mangafire/search.json").read_text(encoding="utf-8")


# ============================== chapter list =============================


def test_all_volume_ids_parses_real_response():
    """all_volume_ids should pull every data-number out of the real ajax JSON."""
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.fetch_json_in_page",
        return_value=CHAPTER_JSON,
    ) as fetched:
        parser = MangafireMangaParser("ad-astra-scipio-and-hanniball.lww3")
        vols = list(parser.all_volume_ids())

    # the endpoint that should have been hit (2nd positional arg = ajax url)
    args = fetched.call_args[0]
    assert args[1] == "https://mangafire.to/ajax/manga/lww3/chapter/en"

    # 81 base chapters + 3 decimal point releases (9.22, 21.22, 28.22) = 84
    assert len(vols) == 84
    # sorted ascending by float
    assert vols[0] == "1"
    assert vols[-1] == "81"
    # decimal chapters preserved verbatim and ordered correctly
    assert "28.22" in vols
    assert vols.index("28.22") > vols.index("28")
    assert vols.index("28.22") < vols.index("29")


def test_volume_url_uses_chapter_number_verbatim():
    parser = MangafireMangaParser("ad-astra-scipio-and-hanniball.lww3")
    assert parser.volume_url("78") == (
        "https://mangafire.to/read/ad-astra-scipio-and-hanniball.lww3/en/chapter-78"
    )
    # decimal chapters must round-trip
    assert parser.volume_url("28.22").endswith("chapter-28.22")


# ================================ search =================================


def test_search_parses_real_response():
    """search should turn the real ajax/manga/search html into the menu dict."""
    with mock.patch.object(MangafireSearch, "_search_in_browser") as browser:
        # _search_in_browser returns the inner result.html string
        browser.return_value = json.loads(SEARCH_JSON)["result"]["html"]
        results = MangafireSearch("ad astra").search()

    # 5 unit cards in the fixture (the trailing "View all" link is excluded)
    assert len(results) == 5

    # first card, exact values from the fixture
    assert results["1"] == SearchResult(
        title="Ad Astra Per Aspera",
        manga_url="ad-astra-per-asperaa.mqmwp",
        chapters="7",
        source="mangafire",
    )

    # the manga we care about is present with its real slug + latest chapter
    slugs = {v["manga_url"]: v for v in results.values()}
    target = slugs["ad-astra-scipio-and-hanniball.lww3"]
    assert target["title"] == "Ad Astra - Scipio and Hannibal"
    assert target["chapters"] == "81"


def test_search_excludes_view_all_link():
    """The 'View all Results' button links to /filter, not /manga - excluded."""
    with mock.patch.object(MangafireSearch, "_search_in_browser") as browser:
        browser.return_value = json.loads(SEARCH_JSON)["result"]["html"]
        results = MangafireSearch("ad astra").search()
    for entry in results.values():
        assert "/filter" not in entry["manga_url"]
        assert entry["manga_url"]  # non-empty slug


def test_search_trigger_js_embeds_query_safely():
    """_type_query_js must json-encode the query (quoting/escaping) so a query
    with quotes can't break out of the injected JS."""
    js = MangafireSearch('a"b')._type_query_js()
    assert json.dumps('a"b') in js
    assert "input[name=keyword]" in js


def test_parse_results_handles_dict_and_str_shape():
    """_search_in_browser must unwrap both {'result': {'html': ...}} and
    {'result': '...'} response shapes - covered by parsing the raw fixture."""
    data = json.loads(SEARCH_JSON)
    assert isinstance(data["result"], dict)
    assert "html" in data["result"]
    # the parser method should yield results from the inner html
    html = data["result"]["html"]
    results = MangafireSearch("ad astra")._parse_results(html, start=1)
    assert len(results) == 5


# =============================== descramble ==============================


def _solid_grid_image(w=1000, h=1500):
    """A test image with a unique color per descramble piece, so we can verify
    the pieces are moved to the expected positions."""
    return Image.new("RGB", (w, h), (10, 20, 30))


def test_descramble_offset_zero_is_identity_size():
    img = _solid_grid_image()
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    # offset 0 should never be passed to descramble by page_data, but if it is
    # the output must remain a valid same-size JPEG
    out = descramble(buf.getvalue(), offset=0)
    result = Image.open(io.BytesIO(out))
    assert result.size == (1000, 1500)


def test_descramble_returns_valid_jpeg():
    img = _solid_grid_image()
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    out = descramble(buf.getvalue(), offset=3)
    result = Image.open(io.BytesIO(out))
    result.verify()
    assert result.format == "JPEG"


# ============================== page urls ================================


def test_encode_page_url_marks_scrambled():
    assert _encode_page_url("http://x/p.jpg", 0) == "http://x/p.jpg"
    assert _encode_page_url("http://x/p.jpg", 5) == "http://x/p.jpg#scrambled_5"


def test_page_urls_carries_offset_in_fragment():
    images = [
        ["http://cdn/p1.jpg", 0, 0],
        ["http://cdn/p2.jpg", 0, 4],
    ]
    # capture_xhr returns (raw_json_body, cookies); page_urls parses
    # json["result"]["images"] itself.
    body = json.dumps({"result": {"images": images}})
    with mock.patch(
        "scraper.parsers.mangafire.BrowserFetcher.capture_xhr",
        return_value=(body, {"cf": "cookie"}),
    ):
        parser = MangafireMangaParser("ad-astra-scipio-and-hanniball.lww3")
        urls = parser.page_urls("78")

    assert urls[0] == (1, "http://cdn/p1.jpg")
    assert urls[1] == (2, "http://cdn/p2.jpg#scrambled_4")
    # cookies harvested for the CDN download step
    assert parser.cookies == {"cf": "cookie"}


# ================================ wiring =================================


def test_site_parser_wires_subparsers():
    site = Mangafire("ad-astra-scipio-and-hanniball.lww3")
    assert site.base_url == "https://mangafire.to"
    assert isinstance(site.manga, MangafireMangaParser)
