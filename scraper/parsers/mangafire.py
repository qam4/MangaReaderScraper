"""
Scraper & parser for https://mangafire.to

How MangaFire works (and why this parser is shaped the way it is):

  * The site is behind Cloudflare, so plain requests fail. We drive a real
    browser (nodriver) for any HTML fetch.
  * A chapter's page images are NOT in the HTML. The reader fires a single
    JSON call ``/ajax/read/chapter/<id>?vrf=<token>`` that returns every
    page's image URL plus a per-page scramble ``offset``. The ``vrf`` token is
    computed by the site's obfuscated JS, so we let the browser fire the call
    and re-fetch that URL from inside the page (Cloudflare-cleared context).
  * Some images are deliberately scrambled (offset > 0): the image is sliced
    into a grid and the slices shifted. We reverse it (descramble) on download.
    Algorithm ported from the keiyoushi Tachiyomi extension's ImageInterceptor.

Image downloads use curl_cffi (Chrome TLS impersonation) with the browser's
cookies + a Referer header, which the CDN requires.
"""

import asyncio
import io
import json
import logging
import re
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup
from PIL import Image

from scraper.exceptions import ChapterDoesntExist, MangaDoesNotExist
from scraper.fetchers import BrowserFetcher, _make_marker_predicate, download_image
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source
from scraper.selection import sort_chapter_ids

logger = logging.getLogger(__name__)

# --- descramble constants (from MangaFire's all.js, mirrored by the extension) ---
PIECE_SIZE = 200
MIN_SPLIT_COUNT = 5

# the page-list ajax calls we want to intercept
_AJAX_MARKERS = ("ajax/read/chapter", "ajax/read/volume")
# fragment we tack onto scrambled image urls so page_data knows to descramble
_SCRAMBLE_TAG = "scrambled"


# ============================== descramble ===============================


def _ceil_div(a: int, b: int) -> int:
    return (a + (b - 1)) // b


def descramble(data: bytes, offset: int) -> bytes:
    """Reverse MangaFire's slice-shuffle. Returns JPEG bytes."""
    src = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = src.size
    out = Image.new("RGB", (w, h))

    piece_w = min(PIECE_SIZE, _ceil_div(w, MIN_SPLIT_COUNT))
    piece_h = min(PIECE_SIZE, _ceil_div(h, MIN_SPLIT_COUNT))
    x_max = _ceil_div(w, piece_w) - 1
    y_max = _ceil_div(h, piece_h) - 1

    for y in range(y_max + 1):
        for x in range(x_max + 1):
            x_dst = piece_w * x
            y_dst = piece_h * y
            pw = min(piece_w, w - x_dst)
            ph = min(piece_h, h - y_dst)

            x_src = piece_w * (x if x == x_max else (x_max - x + offset) % x_max)
            y_src = piece_h * (y if y == y_max else (y_max - y + offset) % y_max)

            piece = src.crop((x_src, y_src, x_src + pw, y_src + ph))
            out.paste(piece, (x_dst, y_dst))

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ========================= browser page-list capture =====================


def _encode_page_url(url: str, offset: int) -> str:
    """Carry the scramble offset in the url fragment, like the Tachiyomi ext."""
    if offset and offset > 0:
        return f"{url}#{_SCRAMBLE_TAG}_{offset}"
    return url


def _authors_from_html(html: str) -> Optional[str]:
    """Extract author(s) from a MangaFire ``/manga/<slug>`` series page.

    Authors are schema.org microdata anchors (``<a itemprop="author">Name</a>``,
    possibly several, in the ``#info-rating`` block). Returns a comma-separated
    string (de-duped, order preserved) or ``None`` if none are present. Pure --
    unit-tested against a captured-shape fixture.
    """
    soup = BeautifulSoup(html, "lxml")
    names: List[str] = []
    for a in soup.select('a[itemprop="author"]'):
        name = a.get_text(strip=True)
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else None


# ================================ parser =================================


class MangafireMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on https://mangafire.to
    """

    def __init__(self, manga_url: str, base_url: str = "https://mangafire.to") -> None:
        super().__init__(manga_url, base_url)
        self.headers = {"Referer": f"{self.base_url}/"}
        # cookies harvested from the browser during page_urls, reused in page_data
        self.cookies: Dict[str, str] = {}

    def chapter_url(self, chapter: str) -> str:
        return f"{self.base_url}/read/{self.manga_url}/en/chapter-{chapter}"

    def page_urls(self, chapter: str) -> List[Tuple[int, str]]:
        """
        Return [(page_number, url)] for every page in a chapter.

        Scrambled pages carry a ``#scrambled_<offset>`` fragment so page_data
        knows to descramble them.
        """
        chapter_url = self.chapter_url(chapter)
        logger.info(f"Fetching page list for {chapter_url}")
        try:
            body, cookies = BrowserFetcher().capture_xhr(
                chapter_url,
                _make_marker_predicate(_AJAX_MARKERS),
                with_cookies=True,
            )
            images = json.loads(body)["result"]["images"]
        except asyncio.TimeoutError:
            raise ChapterDoesntExist(
                f"Timed out getting page list for {self.manga_url} chapter {chapter} "
                "(Cloudflare challenge or chapter does not exist)"
            )
        self.cookies = cookies

        page_urls: List[Tuple[int, str]] = []
        for page_num, entry in enumerate(images, start=1):
            url = entry[0]
            offset = int(entry[2]) if len(entry) > 2 and entry[2] else 0
            page_urls.append((page_num, _encode_page_url(url, offset)))
        return page_urls

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """
        Download a page image (curl_cffi + Chrome impersonation) and descramble
        it if the url fragment marks it scrambled. Overrides the base requests
        implementation because the CDN rejects non-browser TLS fingerprints.
        """
        page_num, raw_url = page_url
        url, _, frag = raw_url.partition("#")
        offset = 0
        if frag.startswith(f"{_SCRAMBLE_TAG}_"):
            offset = int(frag.rsplit("_", 1)[-1])

        content = download_image(
            url,
            headers=self.headers,
            cookies=self.cookies or None,
            label=f"page {page_num}",
        )
        if content is None:
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{url}"),
                "missing",
            )

        try:
            if offset > 0:
                content = descramble(content, offset)
            else:
                # validate it is a real image
                img = Image.open(io.BytesIO(content))
                img.verify()
        except Exception as err:
            logger.error(f"page {page_num} at {url} corrupted: {err}")
            return (
                int(page_num),
                self.create_page(f"Page {page_num} corrupted.\n{err}\n{url}"),
                "corrupted",
            )

        return (int(page_num), content, "success")

    def all_chapter_ids(self) -> Iterable[str]:
        """
        Get the list of all chapter numbers for a manga.

        Uses MangaFire's authoritative chapter-list endpoint
        ``/ajax/manga/<id>/chapter/en`` (no vrf token needed), where ``<id>``
        is the part of the slug after the last ``.`` -- e.g.
        ``ad-astra-scipio-and-hanniball.lww3`` -> ``lww3``. The endpoint
        returns JSON ``{"result": "<li> markup>"}`` whose anchors carry
        ``data-number`` attributes. This mirrors the keiyoushi Tachiyomi
        extension (chapterListRequest).
        """
        manga_id = self.manga_url.rsplit(".", 1)[-1]
        manga_page = f"{self.base_url}/manga/{self.manga_url}"
        ajax_url = f"{self.base_url}/ajax/manga/{manga_id}/chapter/en"
        logger.info(f"Chapter list url={ajax_url}")

        try:
            body = BrowserFetcher().fetch_json_in_page(manga_page, ajax_url)
        except asyncio.TimeoutError:
            raise MangaDoesNotExist(
                f"Timed out fetching chapter list for {self.manga_url}"
            )

        try:
            result_html = json.loads(body)["result"]
        except (ValueError, KeyError) as err:
            raise MangaDoesNotExist(
                f"Unexpected chapter-list response for {self.manga_url}: {err}"
            )

        fragment = BeautifulSoup(result_html, "lxml")
        chapter_ids: set[str] = set()
        for tag in fragment.find_all(attrs={"data-number": True}):
            chapter_ids.add(attr(tag, "data-number"))
        # fallback: pull chapter numbers out of hrefs
        if not chapter_ids:
            for a in fragment.find_all("a", href=re.compile(r"chapter-[\d.]+")):
                m = re.search(r"chapter-([\d.]+)", attr(a, "href"))
                if m:
                    chapter_ids.add(m.group(1))

        if not chapter_ids:
            raise MangaDoesNotExist(
                f"No chapters found for {self.manga_url} (bad slug or page blocked)"
            )
        return sort_chapter_ids(chapter_ids)

    def author(self) -> Optional[str]:
        """Author(s) from the ``/manga/<slug>`` series page
        (``a[itemprop="author"]``). Best effort: the page is Cloudflare-walled so
        we drive a browser; any failure returns None (the builder treats author
        as optional). NOTE: this is a second browser navigation on top of
        all_chapter_ids' -- a future optimization could capture the series HTML
        during that existing session.
        """
        series_url = f"{self.base_url}/manga/{self.manga_url}"
        try:
            html = BrowserFetcher().get(series_url).text
        except Exception as err:  # pragma: no cover - browser/network failures
            logger.debug(f"author lookup failed for {series_url}: {err}")
            return None
        return _authors_from_html(html)


class MangafireSearch(BaseSearchParser):
    """
    Parses search queries from mangafire.to.

    MangaFire's keyword search is gated behind a ``vrf`` token computed by the
    site's obfuscated JS, so we can't hit a plain search URL. Instead we mirror
    the keiyoushi Tachiyomi extension: load the site in a browser, type the
    query into the live search box, and intercept the ``ajax/manga/search``
    response the page fires itself. That response is an HTML fragment of result
    cards which we parse for ``/manga/<slug>`` links.
    """

    def __init__(self, query: str, base_url: str = "https://mangafire.to") -> None:
        super().__init__(query, base_url)

    def _parse_results(self, html_fragment: str, start: int) -> SearchResults:
        """
        Parse the search result cards. Real shape (from ajax/manga/search):

            <div class="original card-sm body">
              <a class="unit" href="/manga/<slug>">
                <div class="poster">...</div>
                <div class="info">
                  <h6>Title</h6>
                  <div><span>Status</span><span>Chap 81</span><span>Vol 13</span></div>
                </div>
              </a>
              ...
            </div>
            <div><a class="btn ..." href="/filter?...">View all Results</a></div>

        We only want the ``a.unit`` cards (the trailing "View all" link is not
        a .unit, so it is naturally excluded).
        """
        soup = BeautifulSoup(html_fragment, "lxml")
        metadata: SearchResults = {}
        key = start
        seen = set()
        for unit in soup.select("a.unit"):
            m = re.search(r"/manga/([^/?#]+)", attr(unit, "href"))
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)

            title_tag = unit.find("h6")
            title = title_tag.get_text(strip=True) if title_tag else slug

            # latest chapter: the span that looks like "Chap 81"
            latest_chapter = ""
            for span in unit.select(".info span"):
                txt = span.get_text(strip=True)
                cm = re.search(r"Chap(?:ter)?\s*([\d.]+)", txt, re.I)
                if cm:
                    latest_chapter = cm.group(1)
                    break

            metadata[str(key)] = SearchResult(
                title=title,
                manga_url=slug,
                latest_chapter=latest_chapter,
                source="mangafire",
            )
            key += 1
        return metadata

    def _type_query_js(self) -> str:
        """JS that types the query into the live search box, triggering the
        vrf'd ajax/manga/search call. Pure -- unit-testable."""
        return (
            "(() => {"
            "  const i = document.querySelector("
            "    '.search-inner input[name=keyword], input[name=keyword]');"
            "  if (!i) return false;"
            f"  i.value = {json.dumps(self.query)};"
            "  i.dispatchEvent(new Event('input', {bubbles:true}));"
            "  i.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));"
            "  return true;"
            "})()"
        )

    def _search_in_browser(self) -> str:
        """Load the site, type the query, intercept the vrf'd
        ``ajax/manga/search`` call and return the inner result html.

        The response is JSON shaped ``{"result": {"html": "..."}}`` or
        ``{"result": "..."}``; unwrap either.
        """
        body, _ = BrowserFetcher().capture_xhr(
            f"{self.base_url}/home",
            _make_marker_predicate(("ajax/manga/search",)),
            trigger_js=self._type_query_js(),
        )
        try:
            result = json.loads(body)["result"]
            if isinstance(result, dict):
                return result.get("html", "")
            return result
        except (ValueError, KeyError):
            return body

    def search(self, start: int = 1) -> SearchResults:
        logger.info(f"Searching mangafire for: {self.query}")
        try:
            html_fragment = self._search_in_browser()
        except asyncio.TimeoutError:
            logger.error(
                "MangaFire search timed out (could not capture the vrf'd "
                "ajax/manga/search call). Use a direct manga slug with --manga."
            )
            return {}
        return self._parse_results(html_fragment, start)


@register_source("mangafire")
class Mangafire(BaseSiteParser):
    """
    Scraper & parser for mangafire.to
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://mangafire.to",
            manga_parser=MangafireMangaParser,
            search_parser=MangafireSearch,
        )
