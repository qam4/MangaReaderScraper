"""Authoring-time parser scaffold tooling (Phase 3, Step B).

This module turns a **human-confirmed** field map (recorded as a small TOML
``parser.toml``) into the inputs a code generator needs to emit a new
``scraper/parsers/<site>.py`` and its tests. It is *authoring-time* tooling: it
is imported only by the generator CLI path, never by the runtime scraper. It is
deliberately **pure** -- text/data in, dataclasses out -- with no network,
browser, or file IO (the thin CLI in task 17 reads the file and hands the text
here).

The schema below is the documented contract (Requirement 5.1). It captures
everything the shipped open-API parser (``scraper/parsers/mangabuddy.py`` for
mangak.io) needs so the generator can reproduce that parser from a config:

  * the site / registry names, the base + API URLs, and the chosen fetcher;
  * the **search** step: endpoint template, the JSON path to the items array,
    and the per-item paths to the title and slug;
  * the **chapters** step: endpoint template, the JSON path to the chapters
    list, the per-chapter name/slug paths, and -- for the mangak.io slug->id
    resolution (Requirement 5.3) -- which *search-item* fields supply the
    title's API ``id`` and ``cv``;
  * the **images** step: which of the three observed source modes is used
    (``api`` / ``next_data`` / ``html``) and the path(s)/selector it needs.

Path access in the *generated* parser is done with ``get_by_path`` -- which
already lives in :mod:`scraper.probe` (added in Phase 2 as the inverse of the
locator). The generated code (task 14) will ``from scraper.probe import
get_by_path``; we deliberately do **not** duplicate that helper here.

Slug->id resolution (Requirement 5.3) is modelled across two specs, mirroring
how the mangabuddy parser works: :attr:`SearchSpec.slug` names the search-item
field matched against the requested slug, and :attr:`ChaptersSpec.id_from` /
:attr:`ChaptersSpec.cv_from` name the search-item fields read off that matched
item to fill the chapters endpoint's ``{id}`` / ``{cv}`` placeholders. They live
on :class:`ChaptersSpec` (next to the endpoint that consumes them) to match the
design document.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

# The three image-source modes the config understands (Requirement 5.2):
#   * ``api``       -- a standalone API endpoint returning the image array;
#   * ``next_data`` -- images embedded in a page's Next.js ``__NEXT_DATA__``
#                      JSON (mangak.io's ``pageProps.initialChapter.images``);
#   * ``html``      -- image elements scraped from page HTML via a selector.
VALID_IMAGE_SOURCES = ("api", "next_data", "html")


class ConfigError(Exception):
    """Raised by :func:`load_parser_config` for invalid/incomplete config.

    The message names the offending field (e.g. ``"missing required field:
    search.items"``) or the invalid value, so the author knows exactly what to
    fix (Requirement 5.4). Malformed TOML and an invalid ``images.source`` are
    surfaced as ``ConfigError`` too, never as a raw ``tomllib`` error.
    """


@dataclass
class SearchSpec:
    """How to search the site and read each result item.

    Attributes:
        endpoint: Search endpoint template, relative to the API URL, with a
            ``{query}`` placeholder -- e.g. ``"/titles/search?q={query}"``.
        items: Dotted/indexed JSON path to the array of result items in the
            search response -- e.g. ``"data.items"``.
        title: Path *within a single item* to the manga's display title --
            e.g. ``"name"``.
        slug: Path *within a single item* to the manga's url slug -- e.g.
            ``"slug"``. This is also the field matched against the requested
            slug during slug->id resolution (Requirement 5.3).
    """

    endpoint: str
    items: str
    title: str
    slug: str


@dataclass
class ChaptersSpec:
    """How to list a title's chapters, and how to resolve a slug to its ids.

    Attributes:
        endpoint: Chapters endpoint template, relative to the API URL, with
            ``{id}`` / ``{cv}`` placeholders -- e.g.
            ``"/titles/{id}/chapters?cv={cv}"``.
        list: Dotted/indexed JSON path to the array of chapters in the response
            -- e.g. ``"data.chapters"``.
        chapter_name: Path *within a single chapter* to its display name (which
            carries the human chapter number) -- e.g. ``"name"``.
        chapter_slug: Path *within a single chapter* to its url slug -- e.g.
            ``"slug"``.
        id_from: Which *search-item* field supplies the title's API ``id`` used
            to fill the endpoint's ``{id}`` -- e.g. ``"id"`` (Requirement 5.3).
        cv_from: Which *search-item* field supplies the ``cv`` used to fill the
            endpoint's ``{cv}`` -- e.g. ``"cv"`` (Requirement 5.3).
    """

    endpoint: str
    list: str
    chapter_name: str
    chapter_slug: str
    id_from: str
    cv_from: str


@dataclass
class ImagesSpec:
    """Where a chapter's page images come from, and how to read them.

    The required fields depend on :attr:`source` (Requirement 5.2):

      * ``api``       -> :attr:`endpoint` + :attr:`images_path`
      * ``next_data`` -> :attr:`page_url` + :attr:`images_path`
      * ``html``      -> :attr:`page_url` + :attr:`selector`

    Attributes:
        source: One of :data:`VALID_IMAGE_SOURCES`.
        images_path: Dotted/indexed JSON path to the images array -- within the
            API response (``api``) or the embedded page JSON (``next_data``).
        page_url: Chapter-page URL template to fetch, with ``{base_url}`` /
            ``{slug}`` / ``{chapter_slug}`` placeholders -- used by
            ``next_data`` and ``html``.
        endpoint: Standalone image-list API endpoint template -- used by ``api``.
        selector: CSS selector for the image elements -- used by ``html``.
    """

    source: str
    images_path: str = ""
    page_url: str = ""
    endpoint: str = ""
    selector: str = ""


@dataclass
class ParserConfig:
    """A complete, human-confirmed parser config (Requirement 5.1).

    Attributes:
        site: Human-facing site name (e.g. ``"mangak.io"``); used in docstrings.
        register_as: Name the parser registers under via ``@register_source``
            (e.g. the legacy ``"mangabuddy"``).
        base_url: Site base URL for page requests (e.g. ``"https://mangak.io"``).
        api_url: API base URL the endpoints are relative to (e.g.
            ``"https://api.mangak.io"``).
        fetcher: The chosen fetcher for the data calls -- ``"curl_cffi"``,
            ``"browser"``, or ``"requests"``.
        search: The :class:`SearchSpec`.
        chapters: The :class:`ChaptersSpec`.
        images: The :class:`ImagesSpec`.
    """

    site: str
    register_as: str
    base_url: str
    api_url: str
    fetcher: str
    search: SearchSpec
    chapters: ChaptersSpec
    images: ImagesSpec


def _require(table: dict[str, Any], key: str, ctx: str = "") -> Any:
    """Return ``table[key]`` or raise :class:`ConfigError` naming the field.

    A key that is absent, ``None``, or an empty string counts as omitted
    (Requirement 5.4). ``ctx`` prefixes the field name in the error so it reads
    as ``"missing required field: search.items"`` for nested fields.
    """
    if not isinstance(table, dict) or key not in table or table[key] in (None, ""):
        field = f"{ctx}.{key}" if ctx else key
        raise ConfigError(f"missing required field: {field}")
    return table[key]


def _require_table(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a required ``[key]`` sub-table, raising if missing or not a table."""
    section = _require(data, key)
    if not isinstance(section, dict):
        raise ConfigError(f"field {key!r} must be a table")
    return section


def _build_search(table: dict[str, Any]) -> SearchSpec:
    return SearchSpec(
        endpoint=str(_require(table, "endpoint", "search")),
        items=str(_require(table, "items", "search")),
        title=str(_require(table, "title", "search")),
        slug=str(_require(table, "slug", "search")),
    )


def _build_chapters(table: dict[str, Any]) -> ChaptersSpec:
    return ChaptersSpec(
        endpoint=str(_require(table, "endpoint", "chapters")),
        list=str(_require(table, "list", "chapters")),
        chapter_name=str(_require(table, "chapter_name", "chapters")),
        chapter_slug=str(_require(table, "chapter_slug", "chapters")),
        id_from=str(_require(table, "id_from", "chapters")),
        cv_from=str(_require(table, "cv_from", "chapters")),
    )


def _build_images(table: dict[str, Any]) -> ImagesSpec:
    source = str(_require(table, "source", "images"))
    if source not in VALID_IMAGE_SOURCES:
        valid = ", ".join(VALID_IMAGE_SOURCES)
        raise ConfigError(f"invalid images.source {source!r}: must be one of {valid}")

    # Per-mode required fields (Requirement 5.2).
    if source == "api":
        _require(table, "endpoint", "images")
        _require(table, "images_path", "images")
    elif source == "next_data":
        _require(table, "page_url", "images")
        _require(table, "images_path", "images")
    else:  # "html"
        _require(table, "page_url", "images")
        _require(table, "selector", "images")

    return ImagesSpec(
        source=source,
        images_path=str(table.get("images_path", "")),
        page_url=str(table.get("page_url", "")),
        endpoint=str(table.get("endpoint", "")),
        selector=str(table.get("selector", "")),
    )


def load_parser_config(text: str) -> ParserConfig:
    """Parse and validate a ``parser.toml`` into a :class:`ParserConfig`.

    Pure: ``text`` in, dataclasses out (no IO). Raises :class:`ConfigError`
    -- never a raw ``tomllib`` error -- for malformed TOML, a missing required
    field (the message names it), or an invalid ``images.source`` value
    (Requirement 5.4).
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"invalid TOML: {err}") from err

    return ParserConfig(
        site=str(_require(data, "site")),
        register_as=str(_require(data, "register_as")),
        base_url=str(_require(data, "base_url")),
        api_url=str(_require(data, "api_url")),
        fetcher=str(_require(data, "fetcher")),
        search=_build_search(_require_table(data, "search")),
        chapters=_build_chapters(_require_table(data, "chapters")),
        images=_build_images(_require_table(data, "images")),
    )


# =========================================================================
# Parser generation (Phase 3, Step B output -- Requirement 6).
#
# ``generate_parser`` is a PURE function (Property 7): it takes a validated
# ``ParserConfig`` and returns the source TEXT of a ``scraper/parsers/<site>.py``
# module. It performs no file IO and no network/browser access -- the thin CLI
# (task 17) is what writes the returned string to disk.
#
# The emitted module is a .py SKELETON with the config VALUES baked in as module
# constants (Req 6.6), NOT a runtime config-interpreter: quirks then live in
# editable code. It is structurally the shipped ``mangabuddy.py`` (Req 6.1) and
# wires to the shared building blocks -- the fetchers, ``sort_chapter_ids``,
# ``SearchResult``, chapter-number-from-name parsing, ``get_by_path`` (Req 6.2)
# -- never a direct HTTP/browser library call on the data path. Two image modes
# are emitted directly (``api`` and ``next_data``, Req 6.2/6.3); the ``html``
# mode and any underivable transform (descramble, vrf) are emitted as explicit
# ``NotImplementedError("site-specific: ...")`` hooks, never a fake body
# (Req 6.4).
#
# Implementation note: the generated method bodies read every field through the
# module constants, so they are STATIC text; only the docstring, the imports,
# the constants block, the class-name prefix (``@@P@@``) and the fetcher call
# (``@@APIGET@@``) vary per config. Templates are raw strings so the regexes /
# ``\n`` they contain land verbatim in the generated source.
# =========================================================================


def _pylit(value: str) -> str:
    """Render ``value`` as a double-quoted Python string literal.

    ``json.dumps`` of a ``str`` is a valid double-quoted Python literal with
    correct escaping, and the config values here are plain ASCII URLs/paths.
    """
    return json.dumps(value)


def _class_prefix(cfg: ParserConfig) -> str:
    """CapWords class-name prefix derived from ``register_as``.

    ``"mangabuddy"`` -> ``"Mangabuddy"``, ``"manga-fox"`` -> ``"MangaFox"``.
    """
    parts = re.split(r"[^0-9a-zA-Z]+", cfg.register_as)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _data_fetcher_class(cfg: ParserConfig) -> str:
    """Map the configured fetcher token to its shared fetcher class name."""
    return {
        "curl_cffi": "CurlCffiFetcher",
        "browser": "BrowserFetcher",
        "requests": "RequestsFetcher",
    }.get(cfg.fetcher, "CurlCffiFetcher")


def _api_get_call(cfg: ParserConfig) -> str:
    """The fetcher ``.get(...)`` expression used on the data path.

    ``BrowserFetcher.get`` takes only a url; the lightweight HTTP backends also
    accept ``headers``. Either way the call goes through the shared fetcher
    layer (Req 6.2).
    """
    fetcher = _data_fetcher_class(cfg)
    if fetcher == "BrowserFetcher":
        return f"{fetcher}().get(url)"
    return f"{fetcher}().get(url, headers=self.headers)"


def _fetcher_import_line(cfg: ParserConfig) -> str:
    """The ``from scraper.fetchers import ...`` line, sorted, mode-aware.

    Always imports the data fetcher + ``FetchResult``; the ``next_data`` image
    mode additionally needs ``CurlCffiFetcher`` (curl-first) and
    ``BrowserFetcher`` (fallback), mirroring the shipped mangabuddy ``page_urls``.
    """
    names = {_data_fetcher_class(cfg), "FetchResult"}
    if cfg.images.source == "next_data":
        names.update({"CurlCffiFetcher", "BrowserFetcher"})
    return "from scraper.fetchers import " + ", ".join(sorted(names))


def _stdlib_import_block(cfg: ParserConfig) -> str:
    """Stdlib imports actually used by the generated module (isort-ordered)."""
    lines = ["import logging", "import re"]
    if cfg.images.source == "next_data":
        # _next_data_from_html parses embedded JSON
        lines.insert(0, "import json")
    return "\n".join(lines)


# -------------------------- header / constants ---------------------------

_MODULE_DOCSTRING = r'''"""AUTOGENERATED scaffold by scraper.scaffold -- VERIFY against the live site.

This module was generated from a human-confirmed parser config for @@SITE@@. It
mirrors the structure of a hand-written parser (see
``scraper/parsers/mangabuddy.py``) with the site's endpoints and JSON paths
substituted in as the module constants below.

It is a SCAFFOLD, not a finished parser: the JSON *shapes* it expects come from
captured fixtures, but the live network paths are UNVERIFIED. Site-specific
quirks belong in THIS file -- edit it freely:

  * image descramble / de-obfuscation,
  * vrf / signed-token request params,
  * extra headers or cookies a stage needs,
  * the plain-HTML image mode.

Transforms that cannot be derived from the config are left as explicit hooks
that raise ``NotImplementedError("site-specific: ...")`` rather than guessing a
fake implementation (Req 6.4). Field access uses ``get_by_path`` so a wrong or
missing path surfaces as "not found" rather than a silent mis-parse (Req 6.5).
"""'''


def _render_header(cfg: ParserConfig) -> str:
    """Module docstring + imports + the module ``logger``."""
    docstring = _MODULE_DOCSTRING.replace("@@SITE@@", cfg.site)
    return "\n".join(
        [
            docstring,
            "",
            _stdlib_import_block(cfg),
            "from typing import Dict, Iterable, List, Optional, Tuple",
            "",
            "from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist",
            _fetcher_import_line(cfg),
            "from scraper.new_types import SearchResult, SearchResults",
            "from scraper.parsers.base import (",
            "    BaseMangaParser,",
            "    BaseSearchParser,",
            "    BaseSiteParser,",
            ")",
            "from scraper.probe import get_by_path",
            "from scraper.registry import register_source",
            "from scraper.selection import sort_chapter_ids",
            "",
            "logger = logging.getLogger(__name__)",
        ]
    )


def _render_constants(cfg: ParserConfig) -> str:
    """The baked-in config values as module constants (Req 6.6)."""
    lines = [
        f"API_URL = {_pylit(cfg.api_url)}",
        f"BASE_URL = {_pylit(cfg.base_url)}",
        f"SOURCE = {_pylit(cfg.register_as)}",
        "",
        f"SEARCH_ENDPOINT = {_pylit(cfg.search.endpoint)}",
        f"SEARCH_ITEMS = {_pylit(cfg.search.items)}",
        f"SEARCH_TITLE = {_pylit(cfg.search.title)}",
        f"SEARCH_SLUG = {_pylit(cfg.search.slug)}",
        "",
        f"CHAPTERS_ENDPOINT = {_pylit(cfg.chapters.endpoint)}",
        f"CHAPTERS_LIST = {_pylit(cfg.chapters.list)}",
        f"CHAPTER_NAME = {_pylit(cfg.chapters.chapter_name)}",
        f"CHAPTER_SLUG = {_pylit(cfg.chapters.chapter_slug)}",
        f"ID_FROM = {_pylit(cfg.chapters.id_from)}",
        f"CV_FROM = {_pylit(cfg.chapters.cv_from)}",
        "",
    ]
    source = cfg.images.source
    if source == "next_data":
        lines.append(f"IMAGES_PATH = {_pylit(cfg.images.images_path)}")
        lines.append(f"PAGE_URL = {_pylit(cfg.images.page_url)}")
    elif source == "api":
        lines.append(f"IMAGES_ENDPOINT = {_pylit(cfg.images.endpoint)}")
        lines.append(f"IMAGES_PATH = {_pylit(cfg.images.images_path)}")
    else:  # html
        lines.append(f"PAGE_URL = {_pylit(cfg.images.page_url)}")
        lines.append(f"IMAGES_SELECTOR = {_pylit(cfg.images.selector)}")
    return "\n".join(lines)


# ------------------------------ helpers ----------------------------------

# Tolerant path access. ``get_by_path`` raises on a path that does not resolve;
# the generated parser wraps it so a wrong/missing field path yields ``None``
# (hence empty results -- a visible test failure) rather than crashing mid-parse
# (Req 6.5). Pure.
_GET_HELPER = r'''def _get(obj: object, path: str) -> object:
    """``get_by_path`` but tolerant: returns ``None`` instead of raising on a
    path that does not resolve, so a wrong/missing field path yields empty
    results (a visible test failure) rather than a crash mid-parse (Req 6.5)."""
    try:
        return get_by_path(obj, path)
    except (KeyError, IndexError, TypeError):
        return None'''

# Chapter-number-from-name: a required SHARED building block (Req 6.2). Copied
# verbatim from the shipped mangabuddy parser -- the API's own number is often a
# sequence counter, so the human number is read out of the chapter NAME.
_CHAPTER_NUMBER_HELPER = r'''def _chapter_number_from_name(name: str) -> Optional[str]:
    """Extract the displayed chapter number from a chapter ``name``.

    The API's own number field is often a sequence counter, not the displayed
    number, so we read the human number out of the name instead::

        "Chapter 700.5 : Uzumaki Naruto"         -> "700.5"
        "Vol.72 Chapter 700.1 : Book Of Thunder" -> "700.1"

    Returns ``None`` when no ``chapter`` number is present (notices, "break",
    ...), so such entries are skipped. Pure.
    """
    match = re.search(r"chapter\s*([0-9]+(?:\.[0-9]+)?)", name, re.IGNORECASE)
    return match.group(1) if match else None'''

# Next.js ``__NEXT_DATA__`` extraction -- emitted only for the ``next_data``
# image mode (Req 6.3). Copied verbatim from the shipped mangabuddy parser.
_NEXT_DATA_HELPER = r'''def _next_data_from_html(html: str) -> Optional[dict]:
    """Parse the Next.js ``__NEXT_DATA__`` JSON embedded in a page's HTML.

    The chapter page server-renders its full payload (including the images
    array) inside ``<script id="__NEXT_DATA__">``, so the image list is read
    straight from the page HTML -- no second request, no ``buildId`` dependency.
    Returns ``None`` if the script is absent or does not parse. Pure.
    """
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None'''

# Search-payload mapping: items via SEARCH_ITEMS, per-item title/slug via
# SEARCH_TITLE / SEARCH_SLUG (Req 6.2). The displayed ``chapters`` count is not
# part of the field map (site-specific), so it is left blank -- fill in if the
# site exposes it.
_PARSE_SEARCH_HELPER = r'''def _parse_search_items(payload: object, start: int = 1) -> SearchResults:
    """Map a search payload to ``SearchResults`` using the configured paths.

    Reads the items array at ``SEARCH_ITEMS`` and each item's title/slug at
    ``SEARCH_TITLE`` / ``SEARCH_SLUG`` via ``get_by_path`` (Req 6.2). The
    ``chapters`` count is not part of the confirmed field map, so it is left
    blank -- fill it in if the site exposes it. Pure mapping logic.
    """
    raw_items = _get(payload, SEARCH_ITEMS)
    items = raw_items if isinstance(raw_items, list) else []
    results: SearchResults = {}
    for key, item in enumerate(items, start=start):
        title = _get(item, SEARCH_TITLE)
        slug = _get(item, SEARCH_SLUG)
        results[str(key)] = SearchResult(
            title=str(title) if title is not None else "",
            manga_url=str(slug) if slug is not None else "",
            chapters="",
            source=SOURCE,
        )
    return results'''

# Chapters-payload mapping: list via CHAPTERS_LIST, per-chapter name/slug via
# CHAPTER_NAME / CHAPTER_SLUG, keyed by the number parsed from the NAME (Req
# 6.2). Entries with no parseable number are skipped; first slug per number wins.
_CHAPTER_MAP_HELPER = r'''def _chapter_map_from_payload(payload: object) -> Dict[str, str]:
    """Build ``{chapter_number: chapter_slug}`` from a chapters payload.

    Reads the list at ``CHAPTERS_LIST`` and each chapter's name/slug at
    ``CHAPTER_NAME`` / ``CHAPTER_SLUG``. The human chapter number is parsed from
    the NAME via ``_chapter_number_from_name`` (the API's own number is often a
    sequence counter -- a documented gotcha). Entries with no parseable number
    are skipped; the first slug seen for a number wins. Pure mapping logic.
    """
    raw_chapters = _get(payload, CHAPTERS_LIST)
    chapters = raw_chapters if isinstance(raw_chapters, list) else []
    mapping: Dict[str, str] = {}
    for chap in chapters:
        name = _get(chap, CHAPTER_NAME)
        number = _chapter_number_from_name(name) if isinstance(name, str) else None
        slug = _get(chap, CHAPTER_SLUG)
        if number is not None and isinstance(slug, str) and slug:
            if number not in mapping:
                mapping[number] = slug
    return mapping'''


def _render_helpers(cfg: ParserConfig) -> str:
    """Module-level helpers for the generated parser, in dependency order.

    ``_next_data_from_html`` is emitted only for the ``next_data`` image mode
    (the only mode that reads embedded page JSON, Req 6.3).
    """
    helpers = [_GET_HELPER, _CHAPTER_NUMBER_HELPER]
    if cfg.images.source == "next_data":
        helpers.append(_NEXT_DATA_HELPER)
    helpers.extend([_PARSE_SEARCH_HELPER, _CHAPTER_MAP_HELPER])
    return "\n\n\n".join(helpers)


# --------------------------- manga parser --------------------------------

# Head of the per-manga parser: construction, the data-path fetch helper, the
# slug->(id, cv) resolution via the search endpoint (Req 5.3 wired), the chapter
# listing, and the number->slug lookup. ``@@APIGET@@`` is replaced with the
# configured fetcher's ``.get(...)`` call; ``@@P@@`` with the class prefix.
_MANGA_CLASS_HEAD = r'''class @@P@@MangaParser(BaseMangaParser):
    """Parse a specific manga via the site's API (generated scaffold).

    One instance is reused across ``all_volume_ids`` -> ``volume_url`` ->
    ``page_urls`` for a manga, so the resolved title id/cv and the
    ``{number: slug}`` chapter map are cached on the instance.
    """

    def __init__(self, manga_url: str, base_url: str = BASE_URL) -> None:
        super().__init__(manga_url, base_url)
        self.api_url = API_URL
        self.headers = {"Referer": f"{base_url}/"}
        self._title_id: Optional[str] = None
        self._cv: Optional[str] = None
        self._chapter_slugs: Dict[str, str] = {}

    def _api_get(self, url: str) -> object:
        """GET a JSON API url through the configured shared fetcher (Req 6.2).

        Raises for a non-2xx so the 404 -> MangaDoesNotExist handling fires.
        """
        result: FetchResult = @@APIGET@@
        result.raise_for_status()
        return result.json()

    def _resolve_title(self) -> Tuple[str, str]:
        """Resolve the manga slug to its API ``(id, cv)`` via the search step.

        Matches the search item whose ``SEARCH_SLUG`` field equals the requested
        slug, then reads ``ID_FROM`` / ``CV_FROM`` off that item (Req 5.3).
        Cached after the first call.
        """
        if self._title_id is not None and self._cv is not None:
            return self._title_id, self._cv

        query = self.manga_url.replace("-", " ").strip()
        url = API_URL + SEARCH_ENDPOINT.format(query=query)
        payload = self._api_get(url)
        raw_items = _get(payload, SEARCH_ITEMS)
        items = raw_items if isinstance(raw_items, list) else []
        for item in items:
            if _get(item, SEARCH_SLUG) == self.manga_url:
                title_id = _get(item, ID_FROM)
                cv = _get(item, CV_FROM)
                if title_id is not None:
                    self._title_id = str(title_id)
                    self._cv = str(cv) if cv is not None else ""
                    return self._title_id, self._cv
        raise MangaDoesNotExist(
            f"Manga {self.manga_url} not found in {SOURCE} search results"
        )

    def all_volume_ids(self) -> Iterable[str]:
        """All chapter numbers for the manga, in canonical order.

        Resolves the title id, fetches the chapters endpoint, and caches the
        ``{number: slug}`` map so ``volume_url`` can turn a chapter number back
        into the slug a chapter url needs.
        """
        import requests  # type: ignore

        title_id, cv = self._resolve_title()
        url = API_URL + CHAPTERS_ENDPOINT.format(id=title_id, cv=cv)
        logger.info(f"Chapter list url={url}")
        try:
            payload = self._api_get(url)
        except requests.exceptions.HTTPError as err:
            status = getattr(err.response, "status_code", None)
            if status == 404:
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise

        self._chapter_slugs = _chapter_map_from_payload(payload)
        if not self._chapter_slugs:
            raise MangaDoesNotExist(f"No numbered chapters found for {self.manga_url}")
        return sort_chapter_ids(self._chapter_slugs.keys())

    def _chapter_slug_for(self, volume: str) -> str:
        """The site chapter slug for a chapter number, populating the cache if
        needed."""
        if not self._chapter_slugs:
            self.all_volume_ids()
        slug = self._chapter_slugs.get(volume)
        if not slug:
            raise VolumeDoesntExist(f"Chapter {volume} not found for {self.manga_url}")
        return slug'''


# Optional site-specific hooks: emitted on every generated parser as explicit
# extension points (Req 6.4). They are NOT wired into the data path -- they
# raise ``NotImplementedError`` rather than guess a fake body. Enable + call them
# from ``page_data`` / the url builders only if the live site needs them.
_MANGA_HOOKS = r'''    # ---- optional site-specific hooks (NOT wired; enable if the site needs them)
    def _descramble_image(self, image: bytes) -> bytes:
        """Hook: reorder/de-obfuscate a scrambled page image.

        Some readers ship shuffled image tiles that must be reassembled
        client-side. This cannot be derived from the config -- implement it
        against the live site and call it from ``page_data`` if needed.
        """
        raise NotImplementedError("site-specific: image descramble")

    def _sign_request(self, url: str) -> str:
        """Hook: add a vrf / signed token a request needs.

        Some sites require a per-request signature (a "vrf" token) computed in
        JS. This cannot be derived from the config -- implement it and apply it
        to the urls built above if the site rejects unsigned requests.
        """
        raise NotImplementedError("site-specific: vrf / signed request token")'''


# Page-image DOWNLOAD: mirrors the shipped mangabuddy parser -- curl_cffi with
# Chrome impersonation + a Referer, since image CDNs often reject non-browser
# TLS fingerprints. This is the one place a direct library call is allowed (the
# task's shipped download pattern); the DATA path stays on the fetchers.
_PAGE_DATA_METHOD = r'''    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """Download a page image with curl_cffi (Chrome impersonation) + Referer.

        The image CDN may reject non-browser TLS fingerprints, so we impersonate
        Chrome rather than using the base requests downloader.
        """
        from curl_cffi import requests as creq  # type: ignore

        page_num, url = page_url
        attempt, max_tries = 0, 5
        content = b""
        while attempt < max_tries:
            try:
                session = creq.Session(impersonate="chrome")
                resp = session.get(url, headers=self.headers, timeout=60)
                if resp.status_code == 200:
                    content = resp.content
                    break
                logger.warning(
                    f"page {page_num} attempt {attempt + 1}/{max_tries} "
                    f"status {resp.status_code}: {url}"
                )
            except Exception as err:
                logger.warning(
                    f"page {page_num} attempt {attempt + 1}/{max_tries} failed: {err}"
                )
            attempt += 1

        if not content:
            logger.error(f"Download FAILED page {page_num} at {url}")
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{url}"),
                "missing",
            )
        return (int(page_num), content, "success")'''


# ---- per-mode url builder + image extraction (Req 6.2 / 6.3 / 6.4) -------

# ``api`` mode: a standalone API endpoint returns the image array (Req 6.2). The
# endpoint template is formatted with every placeholder it might reference.
_IMAGES_API = r'''    def volume_url(self, volume: str) -> str:
        """API url of the image list for a chapter number."""
        chapter_slug = self._chapter_slug_for(volume)
        title_id, cv = self._resolve_title()
        return API_URL + IMAGES_ENDPOINT.format(
            chapter_slug=chapter_slug,
            slug=self.manga_url,
            id=title_id,
            cv=cv,
        )

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """Return [(page_number, image_url)] from the image-list API."""
        url = self.volume_url(volume)
        logger.info(f"Image list url={url}")
        payload = self._api_get(url)
        raw_images = _get(payload, IMAGES_PATH)
        images = (
            [u for u in raw_images if isinstance(u, str)]
            if isinstance(raw_images, list)
            else []
        )
        if not images:
            raise VolumeDoesntExist(
                f"No page images found for {self.manga_url} chapter {volume}"
            )
        return list(enumerate(images, start=1))'''


# ``next_data`` mode: images are embedded in the chapter page's Next.js JSON
# (Req 6.3). Fetch the page curl_cffi-first with a BrowserFetcher fallback --
# mirroring the shipped mangabuddy ``page_urls`` -- then read ``IMAGES_PATH``
# out of the parsed ``__NEXT_DATA__``.
_IMAGES_NEXT_DATA = r'''    def volume_url(self, volume: str) -> str:
        """URL of the chapter page for a given chapter number."""
        chapter_slug = self._chapter_slug_for(volume)
        return PAGE_URL.format(
            base_url=BASE_URL,
            slug=self.manga_url,
            chapter_slug=chapter_slug,
        )

    def _images_from_page_html(self, html: str) -> List[str]:
        """Pull the ordered image urls out of a chapter page's embedded
        ``__NEXT_DATA__`` at ``IMAGES_PATH``. Empty list if absent/unparseable.

        The page's ``__NEXT_DATA__`` wraps the payload under a top-level
        ``props`` key, whereas the ``_next/data`` endpoint JSON does not, so the
        configured path is tried both directly and under ``props.`` -- mirroring
        the shipped mangabuddy parser's dual-shape handling.
        """
        next_data = _next_data_from_html(html)
        if next_data is None:
            return []
        raw_images = _get(next_data, IMAGES_PATH)
        if not isinstance(raw_images, list) and "props" in next_data:
            raw_images = _get(next_data["props"], IMAGES_PATH)
        return (
            [u for u in raw_images if isinstance(u, str)]
            if isinstance(raw_images, list)
            else []
        )

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """Return [(page_number, image_url)] for every page in a chapter.

        The image list lives in the chapter page's server-rendered
        ``__NEXT_DATA__`` (no public image-list API), and the page host may be
        behind Cloudflare, so:

          1. try ``CurlCffiFetcher`` first (Chrome TLS impersonation, no
             browser), and
          2. only if that comes back without the embedded payload fall back to a
             real browser, which clears Cloudflare.
        """
        chapter_url = self.volume_url(volume)

        images: List[str] = []
        try:
            resp = CurlCffiFetcher().get(chapter_url, headers=self.headers)
            if resp.ok:
                images = self._images_from_page_html(resp.text)
        except Exception as err:
            logger.debug(f"curl_cffi page fetch failed for {chapter_url}: {err}")

        if images:
            logger.info(f"page list via curl_cffi (no browser): {chapter_url}")
        else:
            logger.info(
                f"curl_cffi did not yield the page payload; "
                f"falling back to browser for {chapter_url}"
            )
            page = BrowserFetcher().get(chapter_url)
            images = self._images_from_page_html(page.text)

        if not images:
            raise VolumeDoesntExist(
                f"No page images found for {self.manga_url} chapter {volume}"
            )
        return list(enumerate(images, start=1))'''


# ``html`` mode: NOT auto-derivable into a working body (Req 6.4). Emit an
# explicit hook that raises rather than a fake implementation -- task 18 (or a
# hand edit) implements it against the live site using ``IMAGES_SELECTOR``.
_IMAGES_HTML = r'''    def volume_url(self, volume: str) -> str:
        """URL of the chapter page for a given chapter number."""
        chapter_slug = self._chapter_slug_for(volume)
        return PAGE_URL.format(
            base_url=BASE_URL,
            slug=self.manga_url,
            chapter_slug=chapter_slug,
        )

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """Return [(page_number, image_url)] by scraping the chapter page HTML.

        The plain-HTML image mode is site-specific (lazy-loading, ``data-src``
        vs ``src``, descramble) and is NOT auto-generated. Fetch the page via
        ``volume_url(volume)``, select images with ``IMAGES_SELECTOR``, and
        return them ordered -- implement against the live site.
        """
        raise NotImplementedError(
            "site-specific: html image mode -- implement against the live site "
            "(task 18 / hand-write) using IMAGES_SELECTOR"
        )'''


def _render_images_method(cfg: ParserConfig) -> str:
    """The mode-specific ``volume_url`` + image methods for the manga parser."""
    return {
        "api": _IMAGES_API,
        "next_data": _IMAGES_NEXT_DATA,
        "html": _IMAGES_HTML,
    }[cfg.images.source]


def _render_manga_class(cfg: ParserConfig) -> str:
    """Assemble the full ``<Prefix>MangaParser`` class source."""
    prefix = _class_prefix(cfg)
    head = _MANGA_CLASS_HEAD.replace("@@P@@", prefix).replace(
        "@@APIGET@@", _api_get_call(cfg)
    )
    return "\n\n".join(
        [head, _render_images_method(cfg), _PAGE_DATA_METHOD, _MANGA_HOOKS]
    )


# --------------------------- search + site -------------------------------

_SEARCH_CLASS = r'''class @@P@@Search(BaseSearchParser):
    """Parse search queries via the site's search endpoint (generated scaffold)."""

    def __init__(self, query: str, base_url: str = BASE_URL) -> None:
        super().__init__(query, base_url)
        self.api_url = API_URL
        self.headers = {"Referer": f"{base_url}/"}

    def search(self, start: int = 1) -> SearchResults:
        query = self.query.replace(" ", "+")
        url = API_URL + SEARCH_ENDPOINT.format(query=query)
        logger.info(f"Search url={url}")
        result = @@APIGET@@
        result.raise_for_status()
        return _parse_search_items(result.json(), start=start)'''


_SITE_CLASS = r'''@register_source(SOURCE)
class @@P@@(BaseSiteParser):
    """Site parser for @@SITE@@ (generated scaffold). API-backed."""

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=@@P@@MangaParser,
            search_parser=@@P@@Search,
        )'''


def _render_search_class(cfg: ParserConfig) -> str:
    """Assemble the ``<Prefix>Search`` class source."""
    return _SEARCH_CLASS.replace("@@P@@", _class_prefix(cfg)).replace(
        "@@APIGET@@", _api_get_call(cfg)
    )


def _render_site_class(cfg: ParserConfig) -> str:
    """Assemble the ``@register_source``-decorated ``<Prefix>`` site class."""
    return _SITE_CLASS.replace("@@P@@", _class_prefix(cfg)).replace(
        "@@SITE@@", cfg.site
    )


# ------------------------------ assembler --------------------------------

# Section banners mirror the hand-written parsers' layout for readability.
_BANNER_HELPERS = (
    "# ============================== pure helpers ============================="
)
_BANNER_PARSER = (
    "# ================================ parser ================================="
)


def generate_parser(cfg: ParserConfig) -> str:
    """Generate the ``scraper/parsers/<site>.py`` source for ``cfg``.

    PURE (Property 7): a validated :class:`ParserConfig` in, the module source
    TEXT out -- no file IO, no network/browser. The thin CLI (task 17) writes
    the returned string to disk.

    The emitted module is a SKELETON with the config values baked in as module
    constants (Req 6.6), structurally mirroring the shipped ``mangabuddy.py``
    (Req 6.1) and wired to the shared building blocks (Req 6.2). It emits the
    ``api`` and ``next_data`` image modes directly (Req 6.2/6.3); the ``html``
    mode and underivable transforms are explicit ``NotImplementedError`` hooks,
    never fake bodies (Req 6.4). The result is syntactically valid Python with a
    trailing newline.
    """
    header = _render_header(cfg)
    sections = [
        header,
        _render_constants(cfg),
        _BANNER_HELPERS,
        _render_helpers(cfg),
        _BANNER_PARSER,
        _render_manga_class(cfg),
        _render_search_class(cfg),
        _render_site_class(cfg),
    ]
    return "\n\n\n".join(sections) + "\n"


# =========================================================================
# Test generation (Phase 3, Step B output -- Requirements 6.1, 6.5).
#
# ``generate_tests`` is a PURE function (Property 7): it takes a validated
# ``ParserConfig`` plus a small ``fixtures`` dict naming the captured fixture
# files, and returns the source TEXT of a fixture-backed ``tests/test_<site>.py``
# module. It performs no file IO and no network/browser access -- the thin CLI
# (task 17) is what reads the fixture files (only to confirm they exist) and
# writes the returned string to disk.
#
# The emitted module structurally mirrors the hand-written ``tests/
# test_mangabuddy.py`` (the template): it loads the captured fixtures as text,
# defines an ``_ok(text)`` ``FetchResult`` helper, mocks the GENERATED parser
# module's configured fetcher ``.get`` (and, for the ``next_data`` image mode,
# its ``BrowserFetcher``), and asserts search / chapters / page_urls against the
# fixtures.
#
# Requirement 6.5 (a wrong/missing field path must FAIL the test, not silently
# mis-parse) is enforced by the SHAPE of the assertions: the generated parser
# reads every field through the tolerant ``_get`` (returns ``None`` on a bad
# path) and the mappers then drop empties, so a wrong configured path yields
# EMPTY results. The emitted tests therefore assert NON-EMPTY / specific results
# (a non-empty search with non-blank title+slug, a non-empty sorted chapter
# list, a non-empty list of ``(int, url)`` page tuples). If ``_get`` returned
# ``None`` everywhere, every one of those assertions would fail.
#
# The ``fixtures`` dict shape (all keys optional; only the tests whose fixtures
# are supplied are emitted):
#
#     {
#       "dir":       "tests/test_files/<site>",  # fixture directory; defaults
#                                                # to tests/test_files/<register_as>
#       "search":    "search_xxx.json",   # enables the search test
#       "chapters":  "chapters_xxx.json", # with "search": enables the chapters test
#       "page_html": "chapter_page.html", # next_data image mode: page_urls test
#       "images":    "images_xxx.json",   # api image mode: page_urls test
#       "slug":      "naruto",   # a manga slug PRESENT in the search fixture --
#                                # the chapters test resolves it slug->id, so it
#                                # must match a search item (defaults to "naruto")
#       "query":     "naruto",   # a search query for the search test (default "naruto")
#     }
#
# The generated parser/test module basename is ``cfg.register_as`` (see
# ``parser_module_name``): the parser is written to ``scraper/parsers/<name>.py``
# and the test imports ``from scraper.parsers.<name> import ...``. This is the
# SAME basename the task-17 CLI writes to and the task-16 round-trip test
# imports.
# =========================================================================


def parser_module_name(cfg: ParserConfig) -> str:
    """Python module basename the generated parser + tests live under.

    Convention: the parser is generated to ``scraper/parsers/<register_as>.py``
    and the test to ``tests/test_<register_as>.py``; both refer to the parser as
    ``scraper.parsers.<register_as>``. Returns ``cfg.register_as`` verbatim (it
    is the registry name, already a valid identifier for the shipped sites).
    """
    return cfg.register_as


def parser_module_path(cfg: ParserConfig) -> str:
    """Repo-relative path the generated parser is written to (task 17)."""
    return f"scraper/parsers/{parser_module_name(cfg)}.py"


def test_module_path(cfg: ParserConfig) -> str:
    """Repo-relative path the generated test is written to (task 17)."""
    return f"tests/test_{parser_module_name(cfg)}.py"


def _apply(template: str, mapping: dict[str, str]) -> str:
    """Substitute every ``@@KEY@@`` placeholder in ``template`` from ``mapping``."""
    out = template
    for key, value in mapping.items():
        out = out.replace(key, value)
    return out


def _format_from_import(module: str, members: list[str], line_length: int = 88) -> str:
    """Render a ``from <module> import <members>`` line the way ruff's isort does.

    Collapses to a single line when it fits within ``line_length``; otherwise
    splits one member per line inside parentheses with a trailing comma -- so the
    emitted import block is ruff-check (I001) clean either way.
    """
    single = f"from {module} import " + ", ".join(members)
    if len(single) <= line_length:
        return single
    body = "\n".join(f"    {name}," for name in members)
    return f"from {module} import (\n{body}\n)"


# ------------------------------ test header ------------------------------

_TEST_DOCSTRING = '''"""AUTOGENERATED scaffold test by scraper.scaffold -- fixtures only, NOT live.

Generated for @@SITE@@ alongside ``scraper/parsers/@@MODULE@@.py``. It mirrors the
hand-written ``tests/test_mangabuddy.py``: the fixtures are trimmed-but-authentic
slices captured from the site, so these tests exercise the real parsing logic
against the real JSON/HTML shapes -- but every NETWORK path is MOCKED. The live
questions (does the configured fetcher actually reach the API, does the image CDN
need cookies) can only be answered by a live run; this module does not verify
them.

Requirement 6.5: the generated parser reads each field through a tolerant path
lookup that returns ``None`` on a wrong/missing path, so a mis-configured path
produces EMPTY results. These tests assert NON-EMPTY / specific results, so such
a mis-parse surfaces as a test FAILURE rather than passing silently.
"""'''


def _render_test_imports(
    cfg: ParserConfig,
    *,
    fixture_backed: bool,
    emit_search: bool,
    emit_chapters: bool,
    uses_pytest: bool,
) -> str:
    """Assemble the import block for the generated test (isort-ordered).

    Only the names the emitted tests actually reference are imported, so the
    result is ruff-clean (no unused imports) regardless of which tests are
    emitted.
    """
    prefix = _class_prefix(cfg)
    module = parser_module_name(cfg)

    stdlib = []
    if fixture_backed:
        stdlib.append("from pathlib import Path")
        stdlib.append("from unittest import mock")

    third_party = []
    if uses_pytest:
        third_party.append("import pytest")

    first_party = []
    if emit_chapters:
        first_party.append("from scraper.exceptions import MangaDoesNotExist")
    if fixture_backed:
        first_party.append("from scraper.fetchers import FetchResult")

    # Names imported from the generated parser module: constants (UPPER_CASE)
    # first, then classes (CamelCase) -- matching ruff isort's order-by-type.
    constants = ["BASE_URL"]
    if emit_search or emit_chapters:
        constants += ["API_URL", "SEARCH_ENDPOINT"]
    if emit_chapters:
        constants.append("CHAPTERS_ENDPOINT")
    classes = [prefix, f"{prefix}MangaParser"]
    if emit_search:
        classes.append(f"{prefix}Search")
    members = sorted(set(constants)) + sorted(set(classes))
    first_party.append(_format_from_import(f"scraper.parsers.{module}", members))

    if emit_chapters:
        first_party.append("from scraper.selection import sort_chapter_ids")

    groups = [g for g in (stdlib, third_party, first_party) if g]
    return "\n\n".join("\n".join(group) for group in groups)


def _render_fixture_constants(cfg: ParserConfig, fixtures: dict[str, Any]) -> str:
    """The ``FIXTURES`` dir + the ``read_text`` constants for supplied fixtures."""
    fdir = fixtures.get("dir") or f"tests/test_files/{parser_module_name(cfg)}"
    lines = [f"FIXTURES = Path({_pylit(fdir)})"]
    read = '(FIXTURES / {name}).read_text(encoding="utf-8")'
    if fixtures.get("search"):
        lines.append(f"SEARCH_JSON = {read.format(name=_pylit(fixtures['search']))}")
    if fixtures.get("chapters"):
        lines.append(
            f"CHAPTERS_JSON = {read.format(name=_pylit(fixtures['chapters']))}"
        )
    if cfg.images.source == "next_data" and fixtures.get("page_html"):
        lines.append(
            f"CHAPTER_PAGE_HTML = {read.format(name=_pylit(fixtures['page_html']))}"
        )
    if cfg.images.source == "api" and fixtures.get("images"):
        lines.append(f"IMAGES_JSON = {read.format(name=_pylit(fixtures['images']))}")
    return "\n".join(lines)


_OK_HELPER = """def _ok(text):
    return FetchResult(url="http://x", status=200, text=text)"""


# ------------------------------ test bodies ------------------------------

_SEARCH_TEST = r'''def test_search_hits_api_and_parses():
    # Req 6.5: a wrong SEARCH_ITEMS / SEARCH_TITLE / SEARCH_SLUG path makes the
    # parser's tolerant lookup return None, so title/manga_url come back blank --
    # the NON-EMPTY asserts below then FAIL instead of silently mis-parsing.
    with mock.patch(
        "@@DATATARGET@@",
        return_value=_ok(SEARCH_JSON),
    ) as get:
        results = @@P@@Search(@@QUERY@@).search()
    expected_url = API_URL + SEARCH_ENDPOINT.format(query=@@QUERYURL@@)
    assert get.call_args[0][0] == expected_url
    assert results, "search returned no items -- check SEARCH_ITEMS path"
    assert results["1"].title, "blank title -- check SEARCH_TITLE path"
    assert results["1"].manga_url, "blank slug -- check SEARCH_SLUG path"'''


_CHAPTERS_TEST = r"""def test_all_volume_ids_resolves_then_lists_chapters():
    # 1st fetch resolves slug->id via the search step, 2nd lists that title's
    # chapters. The slug must be present in the search fixture so resolution
    # succeeds.
    parser = @@P@@MangaParser(@@SLUG@@)
    with mock.patch(
        "@@DATATARGET@@",
        side_effect=[_ok(SEARCH_JSON), _ok(CHAPTERS_JSON)],
    ) as get:
        vols = list(parser.all_volume_ids())
    expected_search = API_URL + SEARCH_ENDPOINT.format(query=@@RESOLVEQUERY@@)
    assert get.call_args_list[0][0][0] == expected_search
    # the chapters endpoint (its static prefix, before the {id}/{cv} fills) is hit
    chapters_prefix = API_URL + CHAPTERS_ENDPOINT.split("{")[0]
    assert get.call_args_list[1][0][0].startswith(chapters_prefix)
    # Req 6.5: a wrong CHAPTERS_LIST / CHAPTER_NAME / CHAPTER_SLUG path yields an
    # empty chapter map -> MangaDoesNotExist; a non-empty result in canonical
    # order proves the configured paths resolve.
    assert vols, "no chapters -- check CHAPTERS_LIST / CHAPTER_NAME / CHAPTER_SLUG"
    assert vols == sort_chapter_ids(vols)


def test_all_volume_ids_unknown_slug_raises():
    # a slug absent from the search fixture cannot resolve -> a VISIBLE failure
    # (MangaDoesNotExist) rather than a silent empty result.
    parser = @@P@@MangaParser("scaffold-unknown-slug-zzz")
    with mock.patch(
        "@@DATATARGET@@",
        return_value=_ok(SEARCH_JSON),
    ):
        with pytest.raises(MangaDoesNotExist):
            parser.all_volume_ids()"""


_PAGES_NEXT_DATA_TEST = r"""def test_page_urls_reads_embedded_images():
    # next_data mode: images live in the chapter page's __NEXT_DATA__; the page
    # is fetched curl_cffi-first with a BrowserFetcher fallback. Pre-seed the
    # chapter map so volume_url resolves without an extra fetch.
    parser = @@P@@MangaParser(@@SLUG@@)
    parser._chapter_slugs = {"1": "scaffold-chapter-1"}
    browser = mock.Mock()
    with (
        mock.patch(
            "@@CURLTARGET@@",
            return_value=_ok(CHAPTER_PAGE_HTML),
        ),
        mock.patch(
            "@@BROWSERTARGET@@",
            return_value=browser,
        ),
    ):
        pages = parser.page_urls("1")
    # Req 6.5: a wrong IMAGES_PATH yields no images -> VolumeDoesntExist; a
    # non-empty list of (int, url) pairs proves the configured path resolves.
    assert pages, "no images -- check IMAGES_PATH"
    assert all(isinstance(num, int) and isinstance(url, str) for num, url in pages)
    assert pages[0][0] == 1
    browser.get.assert_not_called()  # curl_cffi cleared the page; no browser"""


_PAGES_API_TEST = r"""def test_page_urls_reads_image_api():
    # api mode: a standalone endpoint returns the image array. Pre-seed the
    # caches so volume_url resolves without extra search/chapters fetches.
    parser = @@P@@MangaParser(@@SLUG@@)
    parser._chapter_slugs = {"1": "scaffold-chapter-1"}
    parser._title_id = "scaffold-id"
    parser._cv = "scaffold-cv"
    with mock.patch(
        "@@DATATARGET@@",
        return_value=_ok(IMAGES_JSON),
    ):
        pages = parser.page_urls("1")
    # Req 6.5: a wrong IMAGES_PATH yields no images -> VolumeDoesntExist; a
    # non-empty list of (int, url) pairs proves the configured path resolves.
    assert pages, "no images -- check IMAGES_PATH"
    assert all(isinstance(num, int) and isinstance(url, str) for num, url in pages)
    assert pages[0][0] == 1"""


_PAGES_HTML_TEST = r"""def test_page_urls_html_mode_is_an_explicit_hook():
    # html mode is a site-specific hook (Req 6.4): page_urls raises
    # NotImplementedError rather than guessing a scrape. Implement it against the
    # live site (task 18 / by hand) and replace this test.
    parser = @@P@@MangaParser(@@SLUG@@)
    parser._chapter_slugs = {"1": "scaffold-chapter-1"}
    with pytest.raises(NotImplementedError):
        parser.page_urls("1")"""


_SITE_TEST = r"""def test_site_parser_wires_subparsers():
    site = @@P@@(@@SLUG@@)
    assert isinstance(site.manga, @@P@@MangaParser)
    assert site.base_url == BASE_URL"""


def generate_tests(cfg: ParserConfig, fixtures: dict[str, Any]) -> str:
    """Generate the ``tests/test_<site>.py`` source for ``cfg`` + ``fixtures``.

    PURE (Property 7): a validated :class:`ParserConfig` and a ``fixtures`` dict
    in, the test-module source TEXT out -- no file IO, no network/browser. The
    thin CLI (task 17) writes the returned string to disk.

    See the module-level banner above for the ``fixtures`` dict shape. Only the
    tests whose fixtures are supplied are emitted; the ``html`` image mode always
    emits a ``NotImplementedError`` hook test (it has no fixture). The result is
    valid, ruff-clean Python with a trailing newline.

    Requirement 6.5 is enforced by asserting NON-EMPTY / specific results, so a
    wrong/missing configured field path (which makes the generated parser's
    tolerant lookup return ``None`` and the mappers drop the empties) surfaces as
    a test FAILURE, never a silent mis-parse.
    """
    prefix = _class_prefix(cfg)
    module = parser_module_name(cfg)
    fetcher_class = _data_fetcher_class(cfg)
    data_target = f"scraper.parsers.{module}.{fetcher_class}.get"
    source = cfg.images.source

    query = str(fixtures.get("query") or "naruto")
    slug = str(fixtures.get("slug") or query or "naruto")
    resolve_query = slug.replace("-", " ").strip()

    emit_search = bool(fixtures.get("search"))
    emit_chapters = bool(fixtures.get("search") and fixtures.get("chapters"))
    if source == "next_data":
        emit_pages = bool(fixtures.get("page_html"))
    elif source == "api":
        emit_pages = bool(fixtures.get("images"))
    else:  # html -- an explicit hook test, no fixture needed
        emit_pages = True

    # Any test that fakes a fetch response reads a fixture file and uses
    # mock + FetchResult + _ok; the html-mode page test and the site test do not.
    fixture_backed = (
        emit_search or emit_chapters or (emit_pages and source in ("api", "next_data"))
    )
    uses_pytest = emit_chapters or (emit_pages and source == "html")

    base_map = {
        "@@P@@": prefix,
        "@@SITE@@": cfg.site,
        "@@MODULE@@": module,
        "@@DATATARGET@@": data_target,
        "@@CURLTARGET@@": f"scraper.parsers.{module}.CurlCffiFetcher.get",
        "@@BROWSERTARGET@@": f"scraper.parsers.{module}.BrowserFetcher",
        "@@QUERY@@": _pylit(query),
        "@@QUERYURL@@": _pylit(query.replace(" ", "+")),
        "@@SLUG@@": _pylit(slug),
        "@@RESOLVEQUERY@@": _pylit(resolve_query),
    }

    docstring = _apply(_TEST_DOCSTRING, base_map)
    imports = _render_test_imports(
        cfg,
        fixture_backed=fixture_backed,
        emit_search=emit_search,
        emit_chapters=emit_chapters,
        uses_pytest=uses_pytest,
    )

    preamble = docstring + "\n\n" + imports
    if fixture_backed:
        preamble += "\n\n" + _render_fixture_constants(cfg, fixtures)

    chunks = [preamble]
    if fixture_backed:
        chunks.append(_OK_HELPER)
    if emit_search:
        chunks.append(_apply(_SEARCH_TEST, base_map))
    if emit_chapters:
        chunks.append(_apply(_CHAPTERS_TEST, base_map))
    if emit_pages:
        page_template = {
            "next_data": _PAGES_NEXT_DATA_TEST,
            "api": _PAGES_API_TEST,
            "html": _PAGES_HTML_TEST,
        }[source]
        chunks.append(_apply(page_template, base_map))
    chunks.append(_apply(_SITE_TEST, base_map))

    return "\n\n\n".join(chunks) + "\n"


# =========================================================================
# CLI (task 17): generate a parser + tests from a confirmed ``parser.toml``.
#
# This is the THIN, impure wrapper around the pure functions above (Property 7):
# it is the ONLY place that touches the filesystem. It reads the config file,
# hands the text to ``load_parser_config`` (surfacing a ``ConfigError`` as a
# clean stderr message + non-zero exit, never a traceback), then writes the
# generated parser + test sources to disk.
#
# Safety first: it NEVER silently overwrites an existing target (this protects
# the shipped ``scraper/parsers/mangabuddy.py`` etc.) -- it refuses unless
# ``--force`` is given. ``--dry-run`` writes nothing and prints the generated
# sources to stdout; ``--out-dir`` redirects output under an arbitrary directory
# for safe experimentation. The generated output is a SCAFFOLD requiring live
# verification (Req 7.2), so the closing message says so and reminds the
# developer to register the new module in ``_SOURCE_MODULES``.
# =========================================================================

# Banner printed between the two sources in ``--dry-run`` mode so a reader (or a
# test) can tell the parser source from the test source on stdout.
_DRYRUN_PARSER_BANNER = "# ===== generated parser: {path} ====="
_DRYRUN_TESTS_BANNER = "# ===== generated tests: {path} ====="


def _build_fixtures(args: argparse.Namespace, cfg: ParserConfig) -> dict[str, Any]:
    """Assemble the ``fixtures`` dict ``generate_tests`` understands from flags.

    Only keys the developer supplied are included; ``generate_tests`` emits only
    the tests whose fixtures are present (the ``html`` image mode always emits
    its ``NotImplementedError`` hook test regardless). ``dir`` defaults to
    ``tests/test_files/<register_as>`` -- matching ``generate_tests``'s own
    default -- and is overridable with ``--fixtures-dir``. Pure: reads ``args``,
    returns a dict; no IO.
    """
    fixtures: dict[str, Any] = {
        "dir": args.fixtures_dir or f"tests/test_files/{cfg.register_as}",
    }
    if args.search:
        fixtures["search"] = args.search
    if args.chapters:
        fixtures["chapters"] = args.chapters
    if args.page_html:
        fixtures["page_html"] = args.page_html
    if args.images:
        fixtures["images"] = args.images
    if args.slug:
        fixtures["slug"] = args.slug
    if args.query:
        fixtures["query"] = args.query
    return fixtures


def _target_paths(cfg: ParserConfig, out_dir: Optional[str]) -> tuple[Path, Path]:
    """Resolve the on-disk (parser, test) paths, honouring ``--out-dir``.

    Without ``--out-dir`` the repo-relative ``parser_module_path`` /
    ``test_module_path`` are used verbatim (so a real run writes into
    ``scraper/parsers/`` + ``tests/``). With ``--out-dir DIR`` both are rooted
    under ``DIR`` (``DIR/scraper/parsers/<name>.py`` + ``DIR/tests/...``), giving
    a safe sandbox that never touches the real tree -- which is what the CLI
    tests use. Pure path arithmetic; no IO.
    """
    parser_rel = parser_module_path(cfg)
    test_rel = test_module_path(cfg)
    base = Path(out_dir) if out_dir else Path(".")
    return base / parser_rel, base / test_rel


def _closing_message(cfg: ParserConfig, parser_path: Path, test_path: Path) -> str:
    """The post-write reminder: what was written + that it is a SCAFFOLD.

    States plainly that the output is unverified scaffolding (Req 7.2) and that
    the developer must add the new module to ``_SOURCE_MODULES`` in
    ``scraper/registry.py`` for ``--source`` to pick it up. Pure string building.
    """
    module = parser_module_name(cfg)
    return "\n".join(
        [
            "",
            f"Wrote scaffold for {cfg.site!r} (registers as {cfg.register_as!r}):",
            f"  parser: {parser_path}",
            f"  tests:  {test_path}",
            "",
            "This is a SCAFFOLD, not a finished parser: the JSON shapes come from "
            "captured fixtures but the LIVE network paths are UNVERIFIED. Before "
            "relying on it:",
            "  1. run the generated tests against the fixtures;",
            "  2. verify it against the live site;",
            "  3. implement any NotImplementedError hooks (descramble / vrf / "
            "html image mode);",
            f"  4. add {f'scraper.parsers.{module}'!r} to _SOURCE_MODULES in "
            "scraper/registry.py so --source picks it up.",
        ]
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Generate ``scraper/parsers/<name>.py`` + ``tests/test_<name>.py`` from a config.

    Returns a process exit code (0 ok, non-zero on error) -- mirroring
    ``scraper.probe.main``. All errors are surfaced as clean stderr messages and
    a non-zero return, never an escaping exception/traceback.
    """
    ap = argparse.ArgumentParser(
        prog="python -m scraper.scaffold",
        description=(
            "Generate a parser module + its fixture-backed tests from a "
            "human-confirmed parser.toml (Step B). The output is a SCAFFOLD "
            "requiring live verification -- it is never silently overwritten."
        ),
    )
    ap.add_argument("config", help="path to the parser.toml config to generate from")

    fx = ap.add_argument_group(
        "fixtures",
        "name the captured fixture files the generated tests load (only the "
        "tests whose fixtures are given are emitted)",
    )
    fx.add_argument(
        "--fixtures-dir",
        help="fixtures directory the generated tests read from "
        "(default: tests/test_files/<register_as>)",
    )
    fx.add_argument("--search", help="search-response fixture (enables search test)")
    fx.add_argument(
        "--chapters",
        help="chapters-response fixture (with --search, enables the chapters test)",
    )
    fx.add_argument(
        "--page-html",
        dest="page_html",
        help="chapter-page HTML fixture (next_data image mode: page_urls test)",
    )
    fx.add_argument(
        "--images",
        help="image-list response fixture (api image mode: page_urls test)",
    )
    fx.add_argument(
        "--slug",
        help="a manga slug PRESENT in the search fixture (default: naruto); the "
        "chapters test resolves it slug->id so it must match a search item",
    )
    fx.add_argument(
        "--query",
        help="a search query for the search test (default: naruto)",
    )

    out = ap.add_argument_group("output")
    out.add_argument(
        "--out-dir",
        dest="out_dir",
        help="root to write under instead of the repo (writes "
        "<out-dir>/scraper/parsers/<name>.py + <out-dir>/tests/test_<name>.py) -- "
        "use a sandbox dir to avoid touching the real tree",
    )
    out.add_argument(
        "--dry-run",
        "--print",
        dest="dry_run",
        action="store_true",
        help="write nothing; print the generated parser + test sources to stdout",
    )
    out.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing target files (off by default so a generate "
        "never clobbers a shipped parser)",
    )

    args = ap.parse_args(argv)

    # 1. Read the config file (IO -- handled here, not in the pure loader).
    try:
        text = Path(args.config).read_text(encoding="utf-8")
    except OSError as err:
        print(f"error: cannot read config {args.config!r}: {err}", file=sys.stderr)
        return 2

    # 2. Parse + validate. ConfigError already names the offending field; surface
    #    its message cleanly (no traceback) and exit non-zero (Req 5.4).
    try:
        cfg = load_parser_config(text)
    except ConfigError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1

    # 3. Generate the pure sources.
    fixtures = _build_fixtures(args, cfg)
    parser_src = generate_parser(cfg)
    tests_src = generate_tests(cfg, fixtures)

    parser_path, test_path = _target_paths(cfg, args.out_dir)

    # 4a. Dry run: print to stdout, write nothing.
    if args.dry_run:
        print(_DRYRUN_PARSER_BANNER.format(path=parser_path))
        print(parser_src)
        print(_DRYRUN_TESTS_BANNER.format(path=test_path))
        print(tests_src)
        return 0

    # 4b. Safety: refuse to overwrite an existing target unless --force.
    existing = [p for p in (parser_path, test_path) if p.exists()]
    if existing and not args.force:
        listed = ", ".join(str(p) for p in existing)
        print(
            f"error: refusing to overwrite existing file(s): {listed}. "
            "Re-run with --force to overwrite, or --out-dir to write elsewhere.",
            file=sys.stderr,
        )
        return 1

    # 4c. Write both files (creating parent dirs as needed).
    for path, src in ((parser_path, parser_src), (test_path, tests_src)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")

    print(_closing_message(cfg, parser_path, test_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
