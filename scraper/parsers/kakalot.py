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

import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import fetch_soup
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

    One instance is reused across all_volume_ids -> volume_url -> page_urls, so
    the ``{number: reader_url}`` map scraped from the series page is cached.
    """

    base_url: str = ""
    manga_path: str = "{base_url}/manga/{slug}"
    page_img_attr: str = "src"

    def __init__(self, manga_url: str, base_url: Optional[str] = None) -> None:
        super().__init__(manga_url, base_url or self.base_url)
        self._chapter_urls: Dict[str, str] = {}

    def _manga_page_url(self) -> str:
        return self.manga_path.format(base_url=self.base_url, slug=self.manga_url)

    def all_volume_ids(self) -> Iterable[str]:
        url = self._manga_page_url()
        logger.debug(f"Manga url={url}")
        try:
            manga_html = fetch_soup(url)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e
        self._chapter_urls = _chapter_map_from_soup(manga_html)
        if not self._chapter_urls:
            raise MangaDoesNotExist(
                f"No chapters found for {self.manga_url} (bad slug or page blocked)"
            )
        return sort_chapter_ids(self._chapter_urls.keys())

    def volume_url(self, volume: str) -> str:
        if not self._chapter_urls:
            self.all_volume_ids()
        url = self._chapter_urls.get(volume)
        if not url:
            raise VolumeDoesntExist(f"Chapter {volume} not found for {self.manga_url}")
        return url

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        try:
            url = self.volume_url(volume)
            logger.debug(f"Volume url={url}")
            volume_html = fetch_soup(url)
            string = re.compile("404 NOT FOUND")
            if volume_html.find_all(string=string, recursive=True):
                raise VolumeDoesntExist(
                    f"Manga {self.manga_url} volume {volume} does not exist"
                )
            return volume_html
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} volume {volume} does not exist")
                raise MangaDoesNotExist(
                    f"Manga {self.manga_url} volume {volume} does not exist"
                )
            raise e

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        volume_html = self._scrape_volume(volume)
        container = volume_html.find("div", {"class": "container-chapter-reader"})
        if not isinstance(container, Tag):
            raise VolumeDoesntExist(
                f"No page-image container for {self.manga_url} chapter {volume}"
            )
        all_img_tags = container.find_all("img")
        all_page_urls = [attr(img, self.page_img_attr) for img in all_img_tags]
        all_page_urls = [u for u in all_page_urls if u]
        return list(enumerate(all_page_urls, start=1))


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

    def _extract_text(self, result: Tag) -> SearchResult:
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
            metadata[str(key)] = self._extract_text(result)
        return metadata
