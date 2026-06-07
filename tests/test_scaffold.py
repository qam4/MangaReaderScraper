"""
Tests for the authoring-time parser scaffold (scraper/scaffold.py).

Covers config loading + validation only (task 13 / Requirement 5.5): a complete
mangak.io ``parser.toml`` round-trips to the values the live ``mangabuddy``
parser uses, missing required fields raise a ``ConfigError`` that names the
field (Requirement 5.4), and the per-image-mode / TOML-syntax error paths are
exercised.

Generator behaviour (``generate_parser`` / ``generate_tests``) is intentionally
NOT tested here -- that lands with tasks 14/16, which will add their sections to
this same module below the banner at the end of the file.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import bs4
import pytest

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import FetchResult
from scraper.registry import _REGISTRY
from scraper.scaffold import (
    VALID_IMAGE_SOURCES,
    ChaptersSpec,
    ConfigError,
    ImagesSpec,
    ParserConfig,
    SearchSpec,
    generate_parser,
    generate_tests,
    load_parser_config,
    main,
)

# ============================== fixtures =================================
# A complete, valid mangak.io parser.toml. Field names mirror exactly what
# load_parser_config()'s _require/_build_* expect (register_as, the
# [search]/[chapters]/[images] tables, id_from/cv_from, images_path, page_url).
# Kept as a reusable constant -- the [images] table is LAST so the "missing
# whole section" test can slice it off by splitting on "[images]".

MANGAKIO_TOML = """\
site = "mangak.io"
register_as = "mangabuddy"
base_url = "https://mangak.io"
api_url = "https://api.mangak.io"
fetcher = "curl_cffi"

[search]
endpoint = "/titles/search?q={query}"
items = "data.items"
title = "name"
slug = "slug"

[chapters]
endpoint = "/titles/{id}/chapters?cv={cv}"
list = "data.chapters"
chapter_name = "name"
chapter_slug = "slug"
id_from = "id"
cv_from = "cv"

[images]
source = "next_data"
images_path = "pageProps.initialChapter.images"
page_url = "{base_url}/{slug}/{chapter_slug}"
"""

# A minimal valid config whose images come from a standalone API endpoint.
API_IMAGES_TOML = """\
site = "example.org"
register_as = "example"
base_url = "https://example.org"
api_url = "https://api.example.org"
fetcher = "requests"

[search]
endpoint = "/search?q={query}"
items = "results"
title = "title"
slug = "slug"

[chapters]
endpoint = "/manga/{id}/chapters?cv={cv}"
list = "chapters"
chapter_name = "name"
chapter_slug = "slug"
id_from = "id"
cv_from = "cv"

[images]
source = "api"
endpoint = "/chapter/{chapter_slug}/images"
images_path = "data.pages"
"""

# A minimal valid config whose images are scraped from page HTML.
HTML_IMAGES_TOML = """\
site = "example.net"
register_as = "examplenet"
base_url = "https://example.net"
api_url = "https://api.example.net"
fetcher = "browser"

[search]
endpoint = "/search?q={query}"
items = "results"
title = "title"
slug = "slug"

[chapters]
endpoint = "/manga/{id}/chapters?cv={cv}"
list = "chapters"
chapter_name = "name"
chapter_slug = "slug"
id_from = "id"
cv_from = "cv"

[images]
source = "html"
page_url = "{base_url}/{slug}/{chapter_slug}"
selector = "div.reader img"
"""


# ===================== full mangak.io round-trip =========================


def test_mangakio_config_round_trips_top_level_fields():
    # Req 5.5: a complete mangak.io config loads and its top-level fields equal
    # the values the live mangabuddy parser uses (API_URL/BASE_URL + curl_cffi).
    cfg = load_parser_config(MANGAKIO_TOML)

    assert isinstance(cfg, ParserConfig)
    assert cfg.site == "mangak.io"
    assert cfg.register_as == "mangabuddy"
    assert cfg.base_url == "https://mangak.io"
    assert cfg.api_url == "https://api.mangak.io"
    assert cfg.fetcher == "curl_cffi"


def test_mangakio_config_round_trips_search_spec():
    # Req 5.5: the search step matches the live parser's
    # /titles/search?q=<query> -> data.items[].{name,slug} contract.
    cfg = load_parser_config(MANGAKIO_TOML)

    assert isinstance(cfg.search, SearchSpec)
    assert "/titles/search" in cfg.search.endpoint
    assert "{query}" in cfg.search.endpoint
    assert cfg.search.items == "data.items"
    assert cfg.search.title == "name"
    assert cfg.search.slug == "slug"


def test_mangakio_config_round_trips_chapters_spec():
    # Req 5.5: the chapters step matches /titles/{id}/chapters?cv={cv} ->
    # data.chapters[].{name,slug}, with slug->id resolution via id/cv.
    cfg = load_parser_config(MANGAKIO_TOML)

    assert isinstance(cfg.chapters, ChaptersSpec)
    assert "/titles/{id}/chapters" in cfg.chapters.endpoint
    assert "{cv}" in cfg.chapters.endpoint
    assert cfg.chapters.list == "data.chapters"
    assert cfg.chapters.chapter_name == "name"
    assert cfg.chapters.chapter_slug == "slug"
    assert cfg.chapters.id_from == "id"
    assert cfg.chapters.cv_from == "cv"


def test_mangakio_config_round_trips_images_spec():
    # Req 5.5: images come from the page's embedded Next.js payload at
    # pageProps.initialChapter.images, with a non-empty page-url template.
    cfg = load_parser_config(MANGAKIO_TOML)

    assert isinstance(cfg.images, ImagesSpec)
    assert cfg.images.source == "next_data"
    assert cfg.images.source in VALID_IMAGE_SOURCES
    assert cfg.images.images_path == "pageProps.initialChapter.images"
    assert cfg.images.page_url  # non-empty template


# ===================== missing-field validation ==========================
# Req 5.4: a missing required field raises ConfigError naming that field. The
# loader emits "missing required field: <ctx.key>"; match on a stable substring
# (regex -- the dot in "search.items" is escaped to be literal).


def test_missing_top_level_field_names_it():
    # drop the top-level api_url
    broken = MANGAKIO_TOML.replace('api_url = "https://api.mangak.io"\n', "")
    with pytest.raises(ConfigError, match="api_url"):
        load_parser_config(broken)


def test_missing_nested_search_field_names_it():
    # drop items from [search]
    broken = MANGAKIO_TOML.replace('items = "data.items"\n', "")
    with pytest.raises(ConfigError, match=r"search\.items"):
        load_parser_config(broken)


def test_missing_nested_chapters_field_names_it():
    # drop id_from from [chapters]
    broken = MANGAKIO_TOML.replace('id_from = "id"\n', "")
    with pytest.raises(ConfigError, match=r"chapters\.id_from"):
        load_parser_config(broken)


def test_missing_whole_images_section_is_reported():
    # remove the entire [images] table (it is the last section)
    broken = MANGAKIO_TOML.split("[images]")[0]
    with pytest.raises(ConfigError, match="images"):
        load_parser_config(broken)


# ===================== invalid images.source =============================


def test_invalid_images_source_is_rejected():
    # Req 5.2: an unsupported source (not api/next_data/html) is a ConfigError.
    broken = MANGAKIO_TOML.replace('source = "next_data"', 'source = "ftp"')
    with pytest.raises(ConfigError, match=r"images\.source"):
        load_parser_config(broken)


# ===================== per-mode required fields ==========================
# Req 5.2: each image source mode has its own required fields.


def test_api_source_requires_endpoint():
    broken = API_IMAGES_TOML.replace(
        'endpoint = "/chapter/{chapter_slug}/images"\n', ""
    )
    with pytest.raises(ConfigError, match=r"images\.endpoint"):
        load_parser_config(broken)


def test_api_source_requires_images_path():
    broken = API_IMAGES_TOML.replace('images_path = "data.pages"\n', "")
    with pytest.raises(ConfigError, match=r"images\.images_path"):
        load_parser_config(broken)


def test_html_source_requires_selector():
    broken = HTML_IMAGES_TOML.replace('selector = "div.reader img"\n', "")
    with pytest.raises(ConfigError, match=r"images\.selector"):
        load_parser_config(broken)


def test_next_data_source_requires_images_path():
    # the mangak.io mode: next_data needs page_url + images_path
    broken = MANGAKIO_TOML.replace(
        'images_path = "pageProps.initialChapter.images"\n', ""
    )
    with pytest.raises(ConfigError, match=r"images\.images_path"):
        load_parser_config(broken)


# ===================== malformed TOML ====================================


def test_invalid_toml_is_wrapped_as_config_error():
    # Req 5.4: malformed TOML surfaces as ConfigError, never a raw tomllib error.
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_parser_config('site = "x"\nthis is not = valid toml [[[')


# ===================== valid api / html smoke ============================
# Req 5.2: the other two image-source modes load cleanly and report their mode.


def test_api_source_config_loads():
    cfg = load_parser_config(API_IMAGES_TOML)
    assert isinstance(cfg.images, ImagesSpec)
    assert cfg.images.source == "api"
    assert cfg.images.endpoint == "/chapter/{chapter_slug}/images"
    assert cfg.images.images_path == "data.pages"


def test_html_source_config_loads():
    cfg = load_parser_config(HTML_IMAGES_TOML)
    assert isinstance(cfg.images, ImagesSpec)
    assert cfg.images.source == "html"
    assert cfg.images.page_url == "{base_url}/{slug}/{chapter_slug}"
    assert cfg.images.selector == "div.reader img"


# =========================================================================
# Generator tests (generate_parser / generate_tests) belong below this
# banner and are added by tasks 14 / 16 -- do not add them here.
# =========================================================================


# =========================================================================
# Generator golden + round-trip tests (task 16 / Requirement 6.5).
#
# These exercise generate_parser / generate_tests. The GOLDEN tests assert the
# emitted SOURCE text (pure, no import). The ROUND-TRIP test (Property 5) writes
# the generated mangak.io parser to a temp module, imports it, and runs the same
# assertions tests/test_mangabuddy.py makes against the real fixtures -- proving
# the generator reproduces the working parser. All paths are mocked/fixture
# backed (Req 7.3: no network/browser). Reuses the MANGAKIO_TOML /
# API_IMAGES_TOML / HTML_IMAGES_TOML constants defined above.
# =========================================================================


# ----------------------------- golden: parser ----------------------------


def test_generated_parser_bakes_in_urls_and_endpoints():
    # Req 6.5/6.1: the config's URLs + endpoint templates are baked into the
    # emitted module so the live parser hits the right hosts/paths.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert 'API_URL = "https://api.mangak.io"' in src
    assert 'BASE_URL = "https://mangak.io"' in src
    assert "/titles/search" in src  # search endpoint
    assert "/titles/{id}/chapters" in src  # chapters endpoint


def test_generated_parser_bakes_in_field_paths():
    # Req 6.5: the confirmed JSON field paths are baked in as module constants.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert 'SEARCH_ITEMS = "data.items"' in src
    assert 'CHAPTERS_LIST = "data.chapters"' in src
    assert 'IMAGES_PATH = "pageProps.initialChapter.images"' in src


def test_generated_parser_wires_shared_building_blocks():
    # Req 6.2: the generated parser goes through the shared building blocks --
    # the configured fetcher, the path helper, and sort_chapter_ids -- never a
    # direct HTTP/browser library call on the data path, and registers itself.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert "CurlCffiFetcher().get(" in src  # fetcher wired for curl_cffi
    # the decorator references the baked-in SOURCE constant, which is "mangabuddy"
    assert "@register_source(SOURCE)" in src
    assert 'SOURCE = "mangabuddy"' in src
    assert "from scraper.probe import get_by_path" in src
    assert "from scraper.selection import sort_chapter_ids" in src


def test_generated_parser_is_labelled_as_a_scaffold():
    # Req 7.2: generated parsers are honestly labelled scaffolds needing live
    # verification (stable substrings from _MODULE_DOCSTRING).
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert "AUTOGENERATED" in src
    assert "scaffold" in src
    assert "VERIFY" in src


def test_generated_parser_next_data_uses_curl_first_browser_fallback():
    # Req 6.3: next_data image mode fetches the page (curl_cffi first, browser
    # fallback) and reads the embedded payload via a _next_data_from_html helper.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert "BrowserFetcher" in src  # curl-first / browser-fallback
    assert "_next_data_from_html" in src


def test_generated_parser_emits_explicit_hooks():
    # Req 6.4: underivable transforms are explicit NotImplementedError hooks,
    # never fake bodies.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    assert 'NotImplementedError("site-specific:' in src


def test_generated_parser_compiles():
    # The emitted module must be syntactically valid Python.
    src = generate_parser(load_parser_config(MANGAKIO_TOML))
    compile(src, "<gen>", "exec")


def test_generated_parser_html_mode_extracts_images_and_compiles():
    # Task 18 / Req 6.2: the plain-HTML image mode now emits a REAL page_urls --
    # it fetch_soups the page, selects IMAGES_SELECTOR, and reads each <img>'s
    # url via the shared attr helper -- never the old NotImplementedError stub.
    html_src = generate_parser(load_parser_config(HTML_IMAGES_TOML))
    # the shared building blocks are wired in (Req 6.2): fetch_soup + attr
    assert "from scraper.fetchers import" in html_src
    assert "fetch_soup" in html_src
    assert "from scraper.parsers._html import attr" in html_src
    # the configured selector + the optional image-attr override are baked in
    assert "IMAGES_SELECTOR" in html_src
    assert "IMAGE_ATTR" in html_src
    assert "soup.select_one(IMAGES_SELECTOR)" in html_src
    # the old "not implemented" html image-mode stub is GONE (it's implemented)
    assert 'NotImplementedError("site-specific: html image mode' not in html_src
    # but the underivable descramble / vrf hooks are still present (Req 6.4)
    assert "site-specific: image descramble" in html_src
    assert "site-specific: vrf / signed request token" in html_src
    compile(html_src, "<gen-html>", "exec")


def test_generated_parser_html_mode_uses_browser_fetch_when_configured():
    # When the html config picks the browser fetcher, page_urls fetches through
    # the SHARED building block as fetch_soup(url, BrowserFetcher()) -- still
    # never a direct browser-library call on the data path (Req 6.2).
    html_src = generate_parser(load_parser_config(HTML_IMAGES_TOML))  # fetcher=browser
    assert "fetch_soup(url, BrowserFetcher())" in html_src
    assert (
        "from scraper.fetchers import "
        "BrowserFetcher, FetchResult, download_image, fetch_soup" in html_src
    )


def test_generated_parser_api_mode_has_images_endpoint_and_compiles():
    # Req 6.2: the standalone-API image mode bakes in an IMAGES_ENDPOINT.
    api_src = generate_parser(load_parser_config(API_IMAGES_TOML))
    assert "IMAGES_ENDPOINT" in api_src
    compile(api_src, "<gen-api>", "exec")


# ----------------------------- golden: tests ------------------------------


def test_generated_tests_reference_fixtures_and_mock_fetcher():
    # Req 6.1/6.5: the emitted test module loads the captured fixtures by name
    # and mocks the configured fetcher's .get on the generated parser module.
    fixtures = {
        "dir": "tests/test_files/mangabuddy",
        "search": "search_naruto.json",
        "chapters": "chapters_naruto.json",
        "page_html": "chapter_page.html",
        "slug": "naruto",
        "query": "naruto",
    }
    src = generate_tests(load_parser_config(MANGAKIO_TOML), fixtures)
    assert "search_naruto.json" in src
    assert "chapter_page.html" in src
    assert "scraper.parsers.mangabuddy.CurlCffiFetcher.get" in src
    compile(src, "<gen-tests>", "exec")


# ---------------------- round-trip: import + run --------------------------
# Generate the mangak.io parser into a TEMP module, import it, and run the
# mangabuddy-equivalent assertions against the real fixtures (Property 5).
#
# A DISTINCT register_as keeps the round-trip from clobbering the real
# "mangabuddy" registration: the generated module registers under
# "mangabuddy_gen" (class prefix "MangabuddyGen"), and each test cleans up its
# sys.modules entry + registry key in a finally block to keep global state
# pristine.

ROUNDTRIP_TOML = MANGAKIO_TOML.replace(
    'register_as = "mangabuddy"', 'register_as = "mangabuddy_gen"'
)

# Same fixtures the hand-written tests/test_mangabuddy.py runs against.
_RT_FIXTURES = Path("tests/test_files/mangabuddy")
SEARCH_JSON = (_RT_FIXTURES / "search_naruto.json").read_text(encoding="utf-8")
CHAPTERS_JSON = (_RT_FIXTURES / "chapters_naruto.json").read_text(encoding="utf-8")
CHAPTER_PAGE_HTML = (_RT_FIXTURES / "chapter_page.html").read_text(encoding="utf-8")


def _ok(text):
    return FetchResult(url="http://x", status=200, text=text)


def _import_generated(src: str, module_name: str, path: Path):
    """Write generated parser ``src`` to ``path`` and import it as ``module_name``.

    Registered in ``sys.modules`` so the module's ``@register_source`` decorator
    runs on exec. The caller MUST pop ``sys.modules[module_name]`` and the
    registry entry in a ``finally`` block (see the round-trip tests) so no global
    state leaks between tests.
    """
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_roundtrip_generated_parser_reproduces_mangabuddy(tmp_path):
    # Property 5 / Req 6.5: the generated parser, imported and run against the
    # real fixtures, produces the same search/chapter/page results the shipped
    # mangabuddy parser does.
    cfg = load_parser_config(ROUNDTRIP_TOML)
    assert cfg.register_as == "mangabuddy_gen"  # distinct: no clobber
    module_name = "gen_mangabuddy_roundtrip"
    mod = _import_generated(
        generate_parser(cfg), module_name, tmp_path / "gen_parser.py"
    )
    try:
        # SEARCH -- mirrors test_search_hits_api_and_parses
        with mock.patch.object(
            mod.CurlCffiFetcher, "get", return_value=_ok(SEARCH_JSON)
        ) as get:
            results = mod.MangabuddyGenSearch("naruto").search()
        assert results["1"].title == "Naruto"
        assert results["1"].manga_url == "naruto"
        assert get.call_args[0][0] == "https://api.mangak.io/titles/search?q=naruto"

        # CHAPTERS -- mirrors test_all_volume_ids_resolves_title_then_lists_chapters
        parser = mod.MangabuddyGenMangaParser("naruto")
        with mock.patch.object(
            mod.CurlCffiFetcher,
            "get",
            side_effect=[_ok(SEARCH_JSON), _ok(CHAPTERS_JSON)],
        ) as get:
            vols = list(parser.all_volume_ids())
        assert vols[0] == "0"
        assert vols[-1] == "700.5"
        assert vols.index("700.1") < vols.index("700.5")
        assert (
            get.call_args_list[1][0][0]
            == "https://api.mangak.io/titles/VYPXkPYz/chapters?cv=1780511268548"
        )

        # VOLUME_URL -- mirrors test_volume_url_maps_number_to_slug
        parser = mod.MangabuddyGenMangaParser("naruto")
        with mock.patch.object(
            mod.CurlCffiFetcher,
            "get",
            side_effect=[_ok(SEARCH_JSON), _ok(CHAPTERS_JSON)],
        ):
            url = parser.volume_url("700.5")
        assert url == "https://mangak.io/naruto/chapter-700-5-uzumaki-naruto"

        # PAGE_URLS (next_data) -- mirrors
        # test_page_urls_uses_curl_cffi_when_it_clears_the_page
        parser = mod.MangabuddyGenMangaParser("naruto")
        parser._chapter_slugs = {"1": "vol-1-chapter-1-uzumaki-naruto"}
        browser = mock.Mock()
        with (
            mock.patch.object(
                mod.CurlCffiFetcher, "get", return_value=_ok(CHAPTER_PAGE_HTML)
            ),
            mock.patch.object(mod, "BrowserFetcher", return_value=browser),
        ):
            pages = parser.page_urls("1")
        assert len(pages) == 4
        assert pages[0][0] == 1
        assert pages[0][1].endswith("7358c3b60772.webp")
        browser.get.assert_not_called()  # curl_cffi cleared the page; no browser

        # UNKNOWN SLUG -- mirrors test_all_volume_ids_unknown_slug_raises
        parser = mod.MangabuddyGenMangaParser("does-not-exist")
        with mock.patch.object(
            mod.CurlCffiFetcher, "get", return_value=_ok(SEARCH_JSON)
        ):
            with pytest.raises(MangaDoesNotExist):
                parser.all_volume_ids()
    finally:
        sys.modules.pop(module_name, None)
        _REGISTRY.pop("mangabuddy_gen", None)


# -------------------- round-trip: wrong path fails loudly -----------------
# Req 6.5: a wrong/missing field path must fail LOUDLY (empty result) rather
# than silently mis-parse.

BADPATH_TOML = MANGAKIO_TOML.replace(
    'register_as = "mangabuddy"', 'register_as = "mangabuddy_badpath"'
).replace('items = "data.items"', 'items = "data.WRONG"')


def test_roundtrip_wrong_search_path_yields_empty(tmp_path):
    # With a wrong search.items path the tolerant _get returns None, so no items
    # are mapped: the search comes back EMPTY (a visible failure) instead of
    # silently mis-parsing.
    cfg = load_parser_config(BADPATH_TOML)
    assert cfg.search.items == "data.WRONG"
    module_name = "gen_mangabuddy_badpath"
    mod = _import_generated(
        generate_parser(cfg), module_name, tmp_path / "gen_parser_badpath.py"
    )
    try:
        with mock.patch.object(
            mod.CurlCffiFetcher, "get", return_value=_ok(SEARCH_JSON)
        ):
            results = mod.MangabuddyBadpathSearch("naruto").search()
        assert results == {}
    finally:
        sys.modules.pop(module_name, None)
        _REGISTRY.pop("mangabuddy_badpath", None)


# =========================================================================
# Task 18 / Req 6.2 + 6.5: plain-HTML image mode round-trips against REAL
# shipped HTML fixtures. Generate an html-mode parser, import it into a temp
# module, mock the generated module's `fetch_soup` to return a BeautifulSoup of
# the captured page, and assert page_urls extracts the right images.
#
# Two real fixtures are the ground truth:
#   * mangakaka -- images in `div.container-chapter-reader`, plain `src` (no
#     data-src), with logo/gohome imgs OUTSIDE the container that must be
#     excluded by the container-scoped find_all("img");
#   * mangafast -- images in `div#Read`; the first img carries the real url in
#     `src`, the lazy ones carry a `data:` placeholder in `src` and the real url
#     in `data-src` -- so extraction must prefer data-src, fall back to src, and
#     never emit the `data:` placeholder.
# A wrong selector must fail LOUDLY (VolumeDoesntExist), not silently mis-parse.
# Each test imports under a DISTINCT register_as and tears down sys.modules +
# the registry key, so no global state leaks and nothing lands in the real tree.

# The real shipped HTML fixtures (ground truth for the html-mode round-trip).
MANGAKAKA_VOLUME_HTML = Path(
    "tests/test_files/mangakaka/dragonball_super_volume_1.html"
).read_text(encoding="utf-8")
MANGAFAST_VOLUME_HTML = Path(
    "tests/test_files/mangafast/dragonball_super_volume_1.html"
).read_text(encoding="utf-8")


def _html_toml(register_as: str, selector: str) -> str:
    """A minimal valid html-mode config (search/chapters mirror MANGAKIO shapes;
    they are not exercised by the page test). No image_attr -> data-src/src
    fallback applies."""
    return f"""\
site = "html-site"
register_as = "{register_as}"
base_url = "https://example.com"
api_url = "https://example.com"
fetcher = "curl_cffi"

[search]
endpoint = "/search?q={{query}}"
items = "data.items"
title = "name"
slug = "slug"

[chapters]
endpoint = "/manga/{{id}}/chapters?cv={{cv}}"
list = "data.chapters"
chapter_name = "name"
chapter_slug = "slug"
id_from = "id"
cv_from = "cv"

[images]
source = "html"
page_url = "{{base_url}}/{{slug}}/{{chapter_slug}}"
selector = "{selector}"
"""


def test_roundtrip_html_mode_mangakaka_container_plain_src(tmp_path):
    # mangakaka: div.container-chapter-reader, 16 plain-`src` page images. The
    # logo/gohome imgs OUTSIDE the container must be excluded (count stays 16,
    # every url under the mkklcdnv5 host), and no `data:` placeholder leaks.
    cfg = load_parser_config(_html_toml("kakalot_gen", "div.container-chapter-reader"))
    module_name = "gen_kakalot_roundtrip"
    mod = _import_generated(
        generate_parser(cfg), module_name, tmp_path / "gen_kakalot.py"
    )
    try:
        parser = mod.KakalotGenMangaParser("dragon-ball-super")
        parser._chapter_slugs = {"1": "chapter-1"}
        soup = bs4.BeautifulSoup(MANGAKAKA_VOLUME_HTML, "lxml")
        with mock.patch.object(mod, "fetch_soup", return_value=soup):
            pages = parser.page_urls("1")
        assert len(pages) == 16
        assert pages[0] == (
            1,
            "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/"
            "chapter_1_the_god_of_destructions_prophetic_dream/1.jpg",
        )
        # logo / gohome imgs (outside the container) are excluded: every kept url
        # is a page image on the mkklcdnv5 host, and none is a data: placeholder.
        assert all("mkklcdnv5.com" in url for _, url in pages)
        assert not any(url.startswith("data:") for _, url in pages)
        # pages are numbered 1..16 in order
        assert [num for num, _ in pages] == list(range(1, 17))
    finally:
        sys.modules.pop(module_name, None)
        _REGISTRY.pop("kakalot_gen", None)


def test_roundtrip_html_mode_mangafast_lazy_data_src(tmp_path):
    # mangafast: div#Read; the FIRST img's real url is in `src` (no data-src),
    # the lazy ones have a `data:` placeholder in `src` and the real url in
    # `data-src`. Extraction must prefer data-src, fall back to src for the first
    # img, and never emit the data: placeholder.
    cfg = load_parser_config(_html_toml("mangafast_gen", "div#Read"))
    module_name = "gen_mangafast_roundtrip"
    mod = _import_generated(
        generate_parser(cfg), module_name, tmp_path / "gen_mangafast.py"
    )
    try:
        parser = mod.MangafastGenMangaParser("dragon-ball-super")
        parser._chapter_slugs = {"1": "chapter-1"}
        soup = bs4.BeautifulSoup(MANGAFAST_VOLUME_HTML, "lxml")
        with mock.patch.object(mod, "fetch_soup", return_value=soup):
            pages = parser.page_urls("1")
        assert pages, "no images extracted from div#Read"
        # page 1: real url read from `src` (the first img has no data-src)
        assert pages[0] == (
            1,
            "https://i0.wp.com/mangafast.net/img4/2020-07-22/576980/"
            "dragon-ball-super-chapter-1-page-1.jpg?q=70",
        )
        # page 2: real url read from `data-src`, NOT the data:image/svg placeholder
        assert pages[1] == (
            2,
            "https://i0.wp.com/mangafast.net/img4/2020-07-22/576980/"
            "dragon-ball-super-chapter-1-page-2.jpg?q=70",
        )
        # the lazy data: placeholder never leaks into the page urls
        assert not any(url.startswith("data:") for _, url in pages)
    finally:
        sys.modules.pop(module_name, None)
        _REGISTRY.pop("mangafast_gen", None)


def test_roundtrip_html_mode_wrong_selector_fails_loudly(tmp_path):
    # Req 6.5: a wrong selector finds no container (select_one -> None), so
    # page_urls raises VolumeDoesntExist -- a LOUD failure, not a silent empty
    # result or mis-parse.
    cfg = load_parser_config(_html_toml("badsel_gen", "div.does-not-exist"))
    module_name = "gen_badsel_roundtrip"
    mod = _import_generated(
        generate_parser(cfg), module_name, tmp_path / "gen_badsel.py"
    )
    try:
        parser = mod.BadselGenMangaParser("dragon-ball-super")
        parser._chapter_slugs = {"1": "chapter-1"}
        soup = bs4.BeautifulSoup(MANGAKAKA_VOLUME_HTML, "lxml")
        with mock.patch.object(mod, "fetch_soup", return_value=soup):
            with pytest.raises(VolumeDoesntExist):
                parser.page_urls("1")
    finally:
        sys.modules.pop(module_name, None)
        _REGISTRY.pop("badsel_gen", None)


# =========================================================================
# CLI tests (task 17 / Requirements 6.1, 7.2).
#
# These exercise scraper.scaffold.main directly with arg lists (never a shell).
# CRITICAL: every test that writes targets --out-dir at tmp_path (or uses
# --dry-run, which writes nothing), so NO test ever creates a file under the
# real scraper/parsers/ or tests/ trees. The CLI is the only impure (file IO)
# surface; the generators it calls stay pure.
# =========================================================================


def _write_toml(tmp_path: Path, text: str = MANGAKIO_TOML) -> Path:
    """Write a parser.toml into tmp_path and return its path (as a str-able)."""
    cfg_path = tmp_path / "parser.toml"
    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path


def test_cli_dry_run_prints_sources_and_writes_nothing(tmp_path, capsys):
    # --dry-run prints the generated parser source to stdout and touches no
    # files (so nothing lands in the real tree, and tmp_path stays clean apart
    # from the config we wrote).
    cfg_path = _write_toml(tmp_path)

    rc = main([str(cfg_path), "--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "class MangabuddyMangaParser" in out
    assert "AUTOGENERATED" in out
    # the only file in tmp_path is the parser.toml we wrote -- nothing generated
    assert list(tmp_path.iterdir()) == [cfg_path]


def test_cli_out_dir_writes_parser_and_tests(tmp_path, capsys):
    # --out-dir roots output under tmp_path: <out>/scraper/parsers/<name>.py +
    # <out>/tests/test_<name>.py. Use a register_as that won't collide and keep
    # everything under tmp_path so the real tree is never touched.
    toml = MANGAKIO_TOML.replace(
        'register_as = "mangabuddy"', 'register_as = "mangacli"'
    )
    cfg_path = _write_toml(tmp_path, toml)
    out_dir = tmp_path / "sandbox"

    rc = main(
        [
            str(cfg_path),
            "--out-dir",
            str(out_dir),
            "--search",
            "search_naruto.json",
            "--chapters",
            "chapters_naruto.json",
            "--page-html",
            "chapter_page.html",
        ]
    )

    assert rc == 0
    parser_file = out_dir / "scraper" / "parsers" / "mangacli.py"
    test_file = out_dir / "tests" / "test_mangacli.py"
    assert parser_file.exists()
    assert test_file.exists()

    parser_src = parser_file.read_text(encoding="utf-8")
    assert "class MangacliMangaParser" in parser_src
    assert 'API_URL = "https://api.mangak.io"' in parser_src

    test_src = test_file.read_text(encoding="utf-8")
    assert "search_naruto.json" in test_src
    assert "chapter_page.html" in test_src

    # closing message names the files + the scaffold/registry reminder (Req 7.2)
    out = capsys.readouterr().out
    assert "SCAFFOLD" in out
    assert "_SOURCE_MODULES" in out


def test_cli_config_error_names_field_and_no_traceback(tmp_path, capsys):
    # A config missing a required field exits non-zero with a stderr message
    # naming the field -- and main RETURNS an int (no exception escapes).
    broken = MANGAKIO_TOML.replace('items = "data.items"\n', "")
    cfg_path = _write_toml(tmp_path, broken)

    rc = main([str(cfg_path), "--out-dir", str(tmp_path / "sandbox")])

    assert rc != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "search.items" in combined
    assert "Traceback" not in combined
    # nothing was generated
    assert not (tmp_path / "sandbox").exists()


def test_cli_refuses_to_overwrite_without_force(tmp_path, capsys):
    # Safety: an existing target is NOT overwritten without --force; main exits
    # non-zero and leaves the original file untouched.
    toml = MANGAKIO_TOML.replace(
        'register_as = "mangabuddy"', 'register_as = "mangacli"'
    )
    cfg_path = _write_toml(tmp_path, toml)
    out_dir = tmp_path / "sandbox"
    parser_file = out_dir / "scraper" / "parsers" / "mangacli.py"
    parser_file.parent.mkdir(parents=True)
    parser_file.write_text("ORIGINAL CONTENT", encoding="utf-8")

    rc = main([str(cfg_path), "--out-dir", str(out_dir)])

    assert rc != 0
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err
    # the pre-existing file is untouched
    assert parser_file.read_text(encoding="utf-8") == "ORIGINAL CONTENT"


def test_cli_force_overwrites_existing_target(tmp_path):
    # With --force an existing target IS overwritten with the generated source.
    toml = MANGAKIO_TOML.replace(
        'register_as = "mangabuddy"', 'register_as = "mangacli"'
    )
    cfg_path = _write_toml(tmp_path, toml)
    out_dir = tmp_path / "sandbox"
    parser_file = out_dir / "scraper" / "parsers" / "mangacli.py"
    parser_file.parent.mkdir(parents=True)
    parser_file.write_text("ORIGINAL CONTENT", encoding="utf-8")

    rc = main([str(cfg_path), "--out-dir", str(out_dir), "--force"])

    assert rc == 0
    overwritten = parser_file.read_text(encoding="utf-8")
    assert overwritten != "ORIGINAL CONTENT"
    assert "class MangacliMangaParser" in overwritten
