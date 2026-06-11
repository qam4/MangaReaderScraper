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
    href, so it can't be reconstructed from the path).
  * Reader page: ``div.container-chapter-reader`` holding the page ``<img>``
    (``src`` for nato/kaka, ``data-src`` for nelo) -- unchanged.
  * Search: ``div.story_item`` cards (title in ``h3.story_name`` / ``img alt``,
    slug from the card link).

Each site subclass just sets a few class attributes (base_url, manga_path,
page_img_attr). LIVE-VERIFICATION CAVEAT: parsing is confirmed against the
captured fixtures; the live fetch is exercised by mocked tests, not a live run.
"""

import io
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag
from PIL import Image

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
    # CSS selector for the reader container holding the page <img>s.
    reader_selector: str = "div.container-chapter-reader"
    # CSS selector for the (infinite-scroll) chapter-list container.
    chapter_list_selector: str = "div.chapter-list"

    def __init__(self, manga_url: str, base_url: Optional[str] = None) -> None:
        super().__init__(manga_url, base_url or self.base_url)
        self._chapter_urls: Dict[str, str] = {}
        # page-image bytes captured by the browser in page_urls, keyed by url;
        # page_data just serves these (the CDN only answers the live browser).
        self._image_bytes: Dict[str, bytes] = {}

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
        return BrowserFetcher(timeout=120)

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
        """Capture every page image for a chapter using the live browser.

        The image CDN serves bytes ONLY to the browser session that rendered the
        reader page (every HTTP client 403s, even with cookies + Referer; the
        images are cross-origin so an in-page fetch is CORS-blocked). So we read
        the bytes of the browser's own image responses via CDP and cache them;
        ``page_data`` then just serves the cache. Slow, but it is the only thing
        that works for this family.
        """
        reader_url = self.chapter_url(chapter)
        logger.info(f"Rendering {reader_url} to capture page images...")
        pairs = self._page_fetcher().fetch_rendered_images(
            reader_url, self.reader_selector, self.page_img_attr
        )
        self._image_bytes = {url: data for url, data in pairs}
        if not self._image_bytes:
            raise ChapterDoesntExist(
                f"No page images for {self.manga_url} chapter {chapter}"
            )
        return [(i, url) for i, (url, _data) in enumerate(pairs, start=1)]

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """Serve a page image from the bytes the browser captured in page_urls.

        No network here -- the CDN only answers the live browser, so the bytes
        were already harvested. Validates the image and falls back to a
        placeholder if it is missing/corrupt (same contract as the base).
        """
        page_num, img_url = page_url
        content = self._image_bytes.get(img_url)
        if not content:
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{img_url}"),
                "missing",
            )
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception as err:
            logger.error(f"page {page_num} at {img_url} corrupted: {err}")
            return (
                int(page_num),
                self.create_page(f"Page {page_num} corrupted.\n{err}\n{img_url}"),
                "corrupted",
            )
        return (int(page_num), content, "success")


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
