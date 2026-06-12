"""
Shared "kakalot-family" engine.

manganelo (nelomanga), manganato (natomanga) and mangakaka (mangakakalot) are
reskins of the same backend. Re-probed (C2): the family changed since first
written, so the chapter/search stages were rewritten against fresh captures
(tests/test_files/{manganelo,manganato,mangakaka}/). Current shape:

  * Chapter list: the series page ``{base}/manga/<slug>`` renders
    ``div.chapter-list > div.row > span > a`` rows whose ``href`` is the FULL
    reader url (``{base}/manga/<slug>/chapter-<n>``, the number dash-encoded
    e.g. ``chapter-700-6``) and whose text is the displayed number
    ("Chapter 700.6"). Like the mangabuddy parser we map number -> href and
    reuse that href as the reader url (the number lives in the text, not the
    href, so it can't be reconstructed from the path). The list is
    INFINITE-SCROLL (no "show all"), so ``all_chapter_ids`` drives the browser
    to scroll the list to the bottom (BrowserFetcher.get_after_scroll) before
    parsing -- otherwise only the latest ~50 chapters appear.
  * Search: ``div.story_item`` cards (title in ``h3.story_name`` / ``img alt``,
    slug from the card link).

LIMITATION -- page-image download is NOT supported for this family. The reader
page is gated by an INTERACTIVE Cloudflare Turnstile (a human must click the
"Verify you are human" checkbox on every chapter) AND the image CDN
(2xstorage.com / waitst.com, hosts rotate) serves bytes only to that live
browser session. Every extraction path was defeated -- see ``page_urls`` for
the full list. So search + chapter listing work; ``page_urls`` fails fast with
a clear message rather than hanging. (Reader markup, for reference if the site
ever loosens up: ``div.container-chapter-reader`` holding the page ``<img>``,
``src`` for nato/kaka, ``data-src`` for nelo.)

Each site subclass just sets a few class attributes (base_url, manga_path,
page_img_attr). LIVE-VERIFICATION CAVEAT: parsing is confirmed against the
captured fixtures; the live fetch is exercised by mocked tests, not a live run.
"""

import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import ChapterDoesntExist, MangaDoesNotExist
from scraper.fetchers import BrowserFetcher
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr, text
from scraper.parsers.base import BaseMangaParser, BaseSearchParser
from scraper.selection import sort_chapter_ids

logger = logging.getLogger(__name__)


def _chapter_number_from_text(label: str) -> Optional[str]:
    """Displayed chapter number from a chapter label ("Chapter 700.6" -> "700.6").

    Returns ``None`` when there's no chapter number, so the row is skipped.
    Pure / unit-tested.
    """
    match = re.search(r"chapter\s*([0-9]+(?:\.[0-9]+)?)", label, re.IGNORECASE)
    return match.group(1) if match else None


def _chapter_map_from_soup(soup: BeautifulSoup) -> Dict[str, str]:
    """Build ``{chapter_number: reader_url}`` from a series page.

    Chapter rows live in ``div.chapter-list`` as anchors whose text carries the
    displayed number and whose ``href`` is the reader url. First occurrence of a
    number wins. Pure / unit-tested against the captured fixtures.
    """
    container = soup.find("div", {"class": "chapter-list"})
    mapping: Dict[str, str] = {}
    if not isinstance(container, Tag):
        return mapping
    for anchor in container.find_all("a", href=True):
        number = _chapter_number_from_text(text(anchor))
        href = attr(anchor, "href")
        if number is not None and href and number not in mapping:
            mapping[number] = href
    return mapping


class KakalotMangaParser(BaseMangaParser):
    """
    Shared manga parser for the kakalot-family engine. Subclasses set the series
    page url template and the page-image attribute.

    Class attributes (override per site):
      * ``base_url``      -- site base
      * ``manga_path``    -- f-string with ``{base_url}``, ``{slug}`` for the
        series (chapter-list) page
      * ``page_img_attr`` -- ``"src"`` or ``"data-src"`` for the reader images

    One instance is reused across all_chapter_ids -> chapter_url -> page_urls, so
    the ``{number: reader_url}`` map scraped from the series page is cached.
    """

    base_url: str = ""
    manga_path: str = "{base_url}/manga/{slug}"
    page_img_attr: str = "src"
    # CSS selector for the (infinite-scroll) chapter-list container.
    chapter_list_selector: str = "div.chapter-list"

    def __init__(self, manga_url: str, base_url: Optional[str] = None) -> None:
        super().__init__(manga_url, base_url or self.base_url)
        self._chapter_urls: Dict[str, str] = {}

    def _manga_page_url(self) -> str:
        return self.manga_path.format(base_url=self.base_url, slug=self.manga_url)

    def _page_fetcher(self) -> BrowserFetcher:
        """Fetcher for the series + reader pages.

        The family gates these pages behind a Cloudflare browser-verification
        wall (like search), so they must be fetched with a real browser. The
        challenge can be slow/intermittent, so allow a generous timeout; the
        positive ``ready_selector`` wait returns as soon as the real content
        actually renders, so a fast clear isn't penalised.
        """
        return BrowserFetcher(timeout=180)

    def all_chapter_ids(self) -> Iterable[str]:
        url = self._manga_page_url()
        logger.debug(f"Manga url={url}")
        # The chapter list is lazy-loaded on scroll (no "show all" control), so
        # drive the browser to scroll the list to the bottom until it stops
        # growing -- otherwise only the latest ~50 chapters are present.
        result = self._page_fetcher().get_after_scroll(
            url,
            count_selector=f"{self.chapter_list_selector} a",
            scroll_selector=self.chapter_list_selector,
        )
        manga_html = BeautifulSoup(result.text, "lxml")
        self._chapter_urls = _chapter_map_from_soup(manga_html)
        if not self._chapter_urls:
            raise MangaDoesNotExist(
                f"No chapters found for {self.manga_url} (bad slug or page blocked)"
            )
        chapter_ids = sort_chapter_ids(self._chapter_urls.keys())
        logger.debug(
            f"Parsed {len(chapter_ids)} chapters for {self.manga_url} "
            f"(first={chapter_ids[:3]}, last={chapter_ids[-3:]})"
        )
        return chapter_ids

    def chapter_url(self, chapter: str) -> str:
        if not self._chapter_urls:
            self.all_chapter_ids()
        url = self._chapter_urls.get(chapter)
        if not url:
            raise ChapterDoesntExist(
                f"Chapter {chapter} not found for {self.manga_url}"
            )
        return url

    def page_urls(self, chapter: str) -> List[Tuple[int, str]]:
        """Page-image download is NOT supported for this family.

        The reader page sits behind an interactive Cloudflare Turnstile (a human
        must click "Verify you are human" on EVERY chapter) AND the image CDN
        serves bytes only to that live browser session. Every extraction path we
        tried is defeated: curl_cffi / cloudscraper (403 even with cookies +
        Referer), direct navigation (blocked: top-level nav vs image
        sub-resource), Network.getResponseBody (-32000, body not retained),
        canvas (cross-origin taint), and Fetch.getResponseBody (nodriver routes
        the call to a different CDP session -> "Fetch domain not enabled", and
        pausing responses hangs the page). Search and chapter listing work; bulk
        page-image download does not.

        We fail fast here (rather than open and hang a browser) so the rest of
        the run stays responsive. See the README limitation note.
        """
        raise ChapterDoesntExist(
            f"{self.manga_url} chapter {chapter}: page-image download is not "
            "supported for this source -- its reader is behind an interactive "
            "Cloudflare Turnstile and a browser-only image CDN (see README). "
            "Search and chapter listing work; downloading page images does not."
        )


class KakalotSearchParser(BaseSearchParser):
    """
    Shared search parser for the kakalot-family engine.

    Result cards are ``div.story_item``; subclasses may override the source name.
    """

    base_url: str = ""
    source: str = ""
    result_selector: str = "div.story_item"

    def __init__(self, query: str, base_url: Optional[str] = None) -> None:
        super().__init__(query, base_url or self.base_url)

    def _title(self, result: Tag) -> str:
        name = result.find(class_="story_name")
        if isinstance(name, Tag) and text(name).strip():
            return text(name).strip()
        return attr(result.find("img"), "alt").strip()

    def _slug(self, result: Tag) -> str:
        manga_url = attr(result.find("a"), "href")
        return Path(manga_url).stem.split("/")[-1]

    def _latest_chapter(self, result: Tag) -> str:
        """Latest-chapter number off the card, best-effort (display-only).

        Looks for a chapter-looking anchor and reads the number from its text;
        returns "" when the card doesn't carry one.
        """
        for anchor in result.find_all("a", href=True):
            number = _chapter_number_from_text(text(anchor))
            if number:
                return number
        return ""

    def _parse_search_result(self, result: Tag) -> SearchResult:
        return SearchResult(
            title=self._title(result),
            manga_url=self._slug(result),
            latest_chapter=self._latest_chapter(result),
            source=self.source,
        )

    def search(self, start: int = 1) -> SearchResults:
        url = f"{self.base_url}/search/story/{self.query.replace(' ', '_')}"
        logger.debug(f"search_url={url}")
        results = self._scrape_results(url, self.result_selector)
        metadata: SearchResults = {}
        for key, result in enumerate(results, start=start):
            metadata[str(key)] = self._parse_search_result(result)
        return metadata
