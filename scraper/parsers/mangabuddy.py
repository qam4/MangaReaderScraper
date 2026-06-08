"""
Scraper & parser for mangak.io (formerly mangabuddy.com, which now redirects
there). Registered under the legacy name ``mangabuddy``.

How mangak.io works (discovered with ``scraper probe`` -- see the captures in
tests/test_files/mangabuddy/ and docs/adding-a-source.md):

  * The site is a Next.js SPA. The **HTML** front-end (``mangak.io``) is behind
    Cloudflare, so plain requests to pages are challenged. But the **API**
    (``api.mangak.io``) is open -- it returns JSON to a normal client, so we use
    the lightweight ``CurlCffiFetcher`` for the data calls and only fall back to
    a browser for the Cloudflare-walled chapter page.

  * Search:       GET api.mangak.io/titles/search?q=<query>
                  -> data.items[]: {id, slug, name, stats.chapters_count,
                     latest_chapters[]}
  * Chapter list: GET api.mangak.io/titles/<id>/chapters?cv=<cv>
                  -> data.chapters[]: {id, slug, name, chapter_number}
  * Chapter page: mangak.io/_next/data/<buildId>/<slug>/<chapter-slug>.json
                  -> pageProps.initialChapter.images[]  (direct .webp urls)

Two important subtleties, both confirmed in the captures:

  1. The API's ``chapter_number`` field is a monotonic *sequence counter*, NOT
     the displayed chapter number. "Chapter 700.5" has ``chapter_number: 748``.
     The human number we select on lives in the ``name`` ("Chapter 700.5").
     So chapter ids come from the name, parsed by ``_chapter_number_from_name``,
     and selection/ordering is handled by ``scraper.selection`` as usual.

  2. Downloading by ``--manga <slug>`` only gives us the slug; the chapter-list
     API needs the title's API ``id`` + ``cv``. We resolve slug -> (id, cv) via
     the search API, matching the item whose ``slug`` equals the requested slug.

LIVE-VERIFICATION CAVEATS (only the JSON shapes are confirmed, from captures;
the live network paths below are exercised by mocked tests, not a live run):
  * whether ``api.mangak.io`` answers ``CurlCffiFetcher`` specifically (a browser
    tab returns JSON; curl_cffi's Chrome impersonation very likely matches but is
    unconfirmed) -- ``page_urls`` falls back to ``BrowserFetcher`` if not;
  * whether the image CDN (``rx.qvzr*.org``) serves a plain GET or needs a
    Referer/cookies; we send a ``Referer`` and impersonate Chrome, matching the
    mangafire approach;
  * the chapter-image route depends on the Next.js ``buildId``, which changes on
    every site redeploy, so we scrape the *current* one from the chapter page
    rather than hardcoding it.
"""

import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import (
    BrowserFetcher,
    CurlCffiFetcher,
    FetchResult,
    download_image,
)
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source
from scraper.selection import sort_chapter_ids

logger = logging.getLogger(__name__)

API_URL = "https://api.mangak.io"
BASE_URL = "https://mangak.io"


# ============================== pure helpers =============================


def _chapter_number_from_name(name: str) -> Optional[str]:
    """Extract the displayed chapter number from a chapter ``name``.

    The API's ``chapter_number`` is a sequence counter, not the real number, so
    we read the human number out of the name instead:

        "Chapter 700.5 : Uzumaki Naruto"        -> "700.5"
        "Vol.72 Chapter 700.1 : Book Of Thunder"-> "700.1"
        "Vol.1 Chapter 0 : Naruto Pilot Manga"  -> "0"

    Returns ``None`` when no ``chapter`` number is present (e.g. "Notice.110",
    "Chapter break") -- such entries have no selectable number and are skipped.
    Pure and unit-tested.
    """
    match = re.search(r"chapter\s*([0-9]+(?:\.[0-9]+)?)", name, re.IGNORECASE)
    return match.group(1) if match else None


def _query_from_slug(slug: str) -> str:
    """Turn a manga url slug into a search query: ``dragon-ball`` -> ``dragon
    ball``. Pure and unit-tested."""
    return slug.replace("-", " ").strip()


def _parse_search_items(payload: dict, source: str, start: int) -> SearchResults:
    """Map an ``api.mangak.io/titles/search`` JSON payload to SearchResults.

    Pulls title/slug/latest-chapter from each item. ``latest_chapter`` is the
    latest chapter's *displayed* number (from its name) when available, else the
    ``stats.chapters_count``. Pure and unit-tested.
    """
    items = payload.get("data", {}).get("items", [])
    results: SearchResults = {}
    for key, item in enumerate(items, start=start):
        latest_chapter = ""
        latest = item.get("latest_chapters") or []
        if latest:
            num = _chapter_number_from_name(latest[0].get("name", ""))
            if num:
                latest_chapter = num
        if not latest_chapter:
            count = item.get("stats", {}).get("chapters_count")
            latest_chapter = str(count) if count is not None else ""
        results[str(key)] = SearchResult(
            title=item.get("name", item.get("slug", "")),
            manga_url=item.get("slug", ""),
            latest_chapter=latest_chapter,
            source=source,
        )
    return results


def _chapter_map_from_payload(payload: dict) -> Dict[str, str]:
    """Build ``{chapter_number: chapter_slug}`` from a chapters API payload.

    Skips entries whose name carries no chapter number (notices, "break", etc.).
    Later duplicates of the same number keep the first seen. Pure/unit-tested.
    """
    chapters = payload.get("data", {}).get("chapters", [])
    mapping: Dict[str, str] = {}
    for chap in chapters:
        number = _chapter_number_from_name(chap.get("name", ""))
        slug = chap.get("slug")
        if number is not None and slug and number not in mapping:
            mapping[number] = slug
    return mapping


def _images_from_chapter_payload(payload: dict) -> List[str]:
    """Extract the ordered page-image urls from a chapter payload.

    Handles both shapes we've seen:
      * the page's ``__NEXT_DATA__`` -> ``props.pageProps.initialChapter.images``
      * the ``_next/data`` json     -> ``pageProps.initialChapter.images``
    Pure and unit-tested.
    """
    page_props = payload.get("pageProps")
    if page_props is None:
        page_props = payload.get("props", {}).get("pageProps", {})
    chapter = page_props.get("initialChapter", {}) if page_props else {}
    images = chapter.get("images") or []
    return [url for url in images if isinstance(url, str)]


def _next_data_from_html(html: str) -> Optional[dict]:
    """Parse the Next.js ``__NEXT_DATA__`` JSON embedded in a page's HTML.

    mangak.io server-renders the chapter page with the full payload (including
    ``initialChapter.images``) inside ``<script id="__NEXT_DATA__">``, so we can
    read the image list straight from the page HTML -- no second request to the
    ``_next/data`` endpoint, and no dependency on the deploy ``buildId``. Returns
    ``None`` if the script isn't present or doesn't parse. Pure/unit-tested.
    """
    match = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


# ================================ parser =================================


class MangabuddyMangaParser(BaseMangaParser):
    """
    Parses a specific manga on mangak.io via its open JSON API.

    Lifecycle note: one instance is reused across ``all_volume_ids`` ->
    ``volume_url`` -> ``page_urls`` for a given manga (see MangaBuilder), so we
    cache the resolved title id/cv and the ``{number: slug}`` chapter map on the
    instance after the first lookup.
    """

    def __init__(self, manga_url: str, base_url: str = BASE_URL) -> None:
        super().__init__(manga_url, base_url)
        self.api_url = API_URL
        self.headers = {"Referer": f"{base_url}/"}
        self._title_id: Optional[str] = None
        self._cv: Optional[str] = None
        self._chapter_slugs: Dict[str, str] = {}

    # -- fetch helpers -----------------------------------------------------

    def _api_get(self, url: str) -> dict:
        """GET a JSON API url with the lightweight curl_cffi backend (the API is
        open). Raises for a non-2xx so the 404 -> MangaDoesNotExist handling in
        callers fires."""
        result: FetchResult = CurlCffiFetcher().get(url, headers=self.headers)
        result.raise_for_status()
        return result.json()

    def _resolve_title(self) -> Tuple[str, str]:
        """Resolve the manga slug to its API ``(id, cv)`` via the search API.

        Cached after the first call. Matches the search item whose ``slug``
        equals ``self.manga_url`` exactly.
        """
        if self._title_id is not None and self._cv is not None:
            return self._title_id, self._cv

        query = _query_from_slug(self.manga_url)
        url = f"{self.api_url}/titles/search?q={query}"
        payload = self._api_get(url)
        for item in payload.get("data", {}).get("items", []):
            if item.get("slug") == self.manga_url:
                self._title_id = item.get("id")
                self._cv = str(item.get("cv", ""))
                if self._title_id:
                    return self._title_id, self._cv
        raise MangaDoesNotExist(
            f"Manga {self.manga_url} not found in mangak.io search results"
        )

    # -- BaseMangaParser API ----------------------------------------------

    def all_volume_ids(self) -> Iterable[str]:
        """All chapter numbers for the manga, in canonical order.

        Resolves the title id, fetches the chapters API, and caches the
        ``{number: slug}`` map so ``volume_url`` can turn a chapter number back
        into the slug the chapter-page url needs.
        """
        import requests  # type: ignore

        title_id, cv = self._resolve_title()
        url = f"{self.api_url}/titles/{title_id}/chapters?cv={cv}"
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

    def volume_url(self, volume: str) -> str:
        """URL of the chapter page for a given chapter number.

        Uses the cached ``{number: slug}`` map from ``all_volume_ids``. If the
        map isn't populated yet (e.g. volume_url called first), it is built.
        """
        if not self._chapter_slugs:
            self.all_volume_ids()
        slug = self._chapter_slugs.get(volume)
        if not slug:
            raise VolumeDoesntExist(f"Chapter {volume} not found for {self.manga_url}")
        return f"{self.base_url}/{self.manga_url}/{slug}"

    def _images_from_page_html(self, html: str) -> List[str]:
        """Pull the ordered page-image urls out of a chapter page's HTML via its
        embedded ``__NEXT_DATA__`` payload. Empty list if absent/unparseable."""
        next_data = _next_data_from_html(html)
        if next_data is None:
            return []
        return _images_from_chapter_payload(next_data)

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """Return [(page_number, image_url)] for every page in a chapter.

        The image list lives only in the chapter page's server-rendered
        ``__NEXT_DATA__`` (mangak.io has no public image-list API -- confirmed by
        intercepting every XHR the reader fires). The page host is behind
        Cloudflare, so:

          1. try ``CurlCffiFetcher`` first (Chrome TLS impersonation, no browser
             -- much less invasive and far faster), and
          2. only if that comes back without the embedded payload (challenged)
             fall back to a real browser, which clears Cloudflare.

        Both outcomes are logged so it's never a mystery which path ran.
        """
        chapter_url = self.volume_url(volume)

        # 1. cheap path: curl_cffi with Chrome impersonation
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
            # 2. fallback: drive a browser to clear Cloudflare
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
        return list(enumerate(images, start=1))

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """Download a page image with curl_cffi (Chrome impersonation) + Referer.

        The image CDN may reject non-browser TLS fingerprints (like mangafire),
        so we impersonate Chrome rather than using the base requests downloader.
        """
        page_num, url = page_url
        content = download_image(url, headers=self.headers, label=f"page {page_num}")
        if content is None:
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{url}"),
                "missing",
            )
        return (int(page_num), content, "success")


class MangabuddySearch(BaseSearchParser):
    """
    Parses search queries via mangak.io's open search API.
    """

    def __init__(self, query: str, base_url: str = BASE_URL) -> None:
        super().__init__(query, base_url)
        self.api_url = API_URL
        self.headers = {"Referer": f"{base_url}/"}

    def search(self, start: int = 1) -> SearchResults:
        query = self.query.replace(" ", "+")
        url = f"{self.api_url}/titles/search?q={query}"
        logger.info(f"Search url={url}")
        result = CurlCffiFetcher().get(url, headers=self.headers)
        result.raise_for_status()
        return _parse_search_items(result.json(), source="mangabuddy", start=start)


@register_source("mangabuddy")
class Mangabuddy(BaseSiteParser):
    """
    Scraper & parser for mangak.io (formerly mangabuddy.com). API-backed.
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=MangabuddyMangaParser,
            search_parser=MangabuddySearch,
        )
