"""
Shared "kakalot-family" engine.

manganelo (nelomanga), manganato (natomanga) and mangakaka (mangakakalot) are
reskins of the same backend: ``<li class="a-h">`` chapter lists, a
``container-chapter-reader`` image div, and a ``search/story/`` search. The
only differences are a handful of URL templates and selectors.

This module factors the shared scraping into ``KakalotMangaParser`` /
``KakalotSearchParser`` base classes; each site subclass just sets class
attributes (refactoring-plan §4.2 -- parsers per engine, not per site). Adding
another site on this engine is then ~a dozen lines of parameters.
"""

import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import fetch_soup
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr
from scraper.parsers.base import BaseMangaParser, BaseSearchParser

logger = logging.getLogger(__name__)


class KakalotMangaParser(BaseMangaParser):
    """
    Shared manga parser for the kakalot-family engine. Subclasses set the URL
    templates and the page-image attribute.

    Class attributes (override per site):
      * ``volume_path``  -- f-string with ``{base_url}``, ``{slug}``, ``{volume}``
      * ``manga_path``   -- f-string with ``{base_url}``, ``{slug}``
      * ``page_img_attr``-- ``"src"`` or ``"data-src"`` for the reader images
      * ``chapter_href_sep`` -- separator the chapter number follows in the href
        (``"-"`` for chapter-<n>, ``"_"`` for chapter_<n>)
    """

    base_url: str = ""
    volume_path: str = ""
    manga_path: str = ""
    page_img_attr: str = "src"
    chapter_href_sep: str = "-"

    def __init__(self, manga_url: str, base_url: Optional[str] = None) -> None:
        super().__init__(manga_url, base_url or self.base_url)

    def volume_url(self, volume: str) -> str:
        return self.volume_path.format(
            base_url=self.base_url, slug=self.manga_url, volume=volume
        )

    def _manga_page_url(self) -> str:
        return self.manga_path.format(base_url=self.base_url, slug=self.manga_url)

    def _scrape_volume(self, volume: str) -> Optional[BeautifulSoup]:
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

    def page_urls(self, volume: str) -> Optional[List[Tuple[int, str]]]:
        volume_html = self._scrape_volume(volume)
        if volume_html:
            container = volume_html.find("div", {"class": "container-chapter-reader"})
            all_img_tags = container.find_all("img")  # type: ignore[union-attr]
            all_page_urls = [attr(img, self.page_img_attr) for img in all_img_tags]
            return list(enumerate(all_page_urls, start=1))
        return None

    def _extract_number(self, href: str) -> str:
        """Pull the chapter number off a chapter href, e.g. ``chapter-55`` or
        ``chapter_55`` -> ``55``."""
        return href.split(self.chapter_href_sep)[-1]

    def all_volume_ids(self) -> Iterable[str]:
        try:
            url = self._manga_page_url()
            logger.debug(f"Manga url={url}")
            manga_html = fetch_soup(url)
            volume_tags = manga_html.find_all("li", {"class": "a-h"})
            volume_ids = set(
                self._extract_number(attr(vol.find("a"), "href")) for vol in volume_tags
            )
            logger.debug(f"volume_ids={volume_ids}")
            return volume_ids
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class KakalotSearchParser(BaseSearchParser):
    """
    Shared search parser for the kakalot-family engine. Subclasses set the
    result-card selector, the source name, and how to read title/slug/chapter
    off a card.

    Class attributes (override per site):
      * ``base_url``         -- site base
      * ``source``           -- registry/source name stamped on results
      * ``result_div_class`` -- the search-result card div class
    """

    base_url: str = ""
    source: str = ""
    result_div_class: str = "story_item"

    def __init__(self, query: str, base_url: Optional[str] = None) -> None:
        super().__init__(query, base_url or self.base_url)

    def _title(self, result: Tag) -> str:
        return attr(result.find("img"), "alt")

    def _slug(self, result: Tag) -> str:
        manga_url = attr(result.find("a"), "href")
        return Path(manga_url).stem.split("/")[-1]

    def _chapters(self, result: Tag) -> str:
        """Latest-chapter number off the card. Default: the ``story_chapter``
        anchor's href tail. Subclasses override for other layouts."""
        last = result.find("em", {"class": "story_chapter"})
        if not last:
            return ""
        anchor = last.find("a")  # type: ignore[union-attr]
        if not anchor:
            return ""
        return attr(anchor, "href").split("_")[-1]

    def _extract_text(self, result: Tag) -> SearchResult:
        return SearchResult(
            title=self._title(result),
            manga_url=self._slug(result),
            chapters=self._chapters(result),
            source=self.source,
        )

    def search(self, start: int = 1) -> SearchResults:
        url = f"{self.base_url}/search/story/{self.query.replace(' ', '_')}"
        logger.debug(f"search_url={url}")
        results = self._scrape_results(url, div_class=self.result_div_class)
        metadata: SearchResults = {}
        for key, result in enumerate(results, start=start):
            metadata[str(key)] = self._extract_text(result)
        return metadata
