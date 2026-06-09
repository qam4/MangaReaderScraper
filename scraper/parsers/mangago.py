"""
Scraper & parser for https://www.mangago.me

Re-probed (C2): the site changed since the parser was first written, so the
chapter/image stages were rewritten against fresh captures (see
tests/test_files/mangago/ and docs/adding-a-source.md). Findings:

  * Chapter list: the series page ``/read-manga/<slug>`` server-renders a
    ``table#chapter_table`` of ``<a class="chico" href=".../read-manga/<slug>/
    mr/v<VOL>/c<CHAP>/pg-1/"><b>Vol.NN Ch.NNN</b> : title</a>`` rows. The
    displayed chapter number lives in the anchor's ``<b>`` label (the href
    carries volume/chapter path segments, not a clean number), so -- like the
    mangabuddy parser -- we map number -> the anchor's href and reuse that href
    as the reader url rather than reconstructing it.
  * Reader page: every page is embedded as ``<img id="pageN" class="pageN"
    src="<cdn>">`` (later pages ``display:none``); nav/ui images (arrow,
    backtotop) have no ``pageN`` id and are excluded.
  * Search: ``/r/l_search/?name=<q>`` still renders ``div.row-1`` result cards;
    that stage is unchanged.

The site is Cloudflare-protected and renders chapters/reader pages with JS, so
the data pages are fetched with ``BrowserFetcher`` (headful nodriver clears the
challenge). LIVE-VERIFICATION CAVEAT: the parsing logic is confirmed against the
captured fixtures; the live browser fetch + image-CDN fetch are exercised by
mocked tests, not a live run here.
"""

import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import BrowserFetcher
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr, text
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source
from scraper.selection import sort_chapter_ids

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mangago.me"


# ============================== pure helpers =============================


def _chapter_number_from_label(label: str) -> Optional[str]:
    """Extract the displayed chapter number from a mangago chapter label.

    Labels read like ``"Vol.72 Ch.700.6"`` / ``"Ch.700.5"`` / ``"Chapter 12"``;
    the number follows ``Ch``/``Chapter``. Returns ``None`` when there is no
    chapter number (a volume-only or special entry), so it can be skipped. Pure
    and unit-tested. Operates on the anchor's ``<b>`` label only (not the title
    text after it), so an incidental "ch" in a chapter title can't false-match.
    """
    match = re.search(r"ch(?:apter)?\.?\s*([0-9]+(?:\.[0-9]+)?)", label, re.IGNORECASE)
    return match.group(1) if match else None


def _chapter_map_from_soup(soup: BeautifulSoup) -> Dict[str, str]:
    """Build ``{chapter_number: reader_url}`` from a mangago series page.

    Chapter rows live in ``table#chapter_table`` as ``a.chico`` anchors whose
    ``<b>`` label carries the displayed number and whose ``href`` is the reader
    url. First occurrence of a number wins. Pure / unit-tested against the
    captured fixtures.
    """
    table = soup.find("table", id="chapter_table")
    mapping: Dict[str, str] = {}
    if not isinstance(table, Tag):
        return mapping
    for anchor in table.find_all("a", {"class": "chico"}):
        bold = anchor.find("b")
        label = text(bold) if isinstance(bold, Tag) else text(anchor)
        number = _chapter_number_from_label(label)
        href = attr(anchor, "href")
        if number is not None and href and number not in mapping:
            mapping[number] = href
    return mapping


def _image_urls_from_soup(soup: BeautifulSoup) -> List[str]:
    """Extract the ordered page-image urls from a mangago reader page.

    Every page is embedded as ``<img id="pageN" class="pageN" src="<cdn>">``
    (later pages ``display:none``); nav/ui images (arrow, backtotop) lack a
    ``pageN`` id and are excluded. Ordered by N. Pure / unit-tested.
    """
    page_imgs = soup.find_all("img", id=re.compile(r"^page\d+$"))

    def _page_index(tag: Tag) -> int:
        match = re.match(r"page(\d+)$", attr(tag, "id"))
        return int(match.group(1)) if match else 0

    ordered = sorted((t for t in page_imgs if isinstance(t, Tag)), key=_page_index)
    return [attr(img, "src") for img in ordered if attr(img, "src")]


# ================================ parser =================================


class MangagoMangaParser(BaseMangaParser):
    """
    Parses a specific manga on mangago.me.

    One instance is reused across ``all_volume_ids`` -> ``volume_url`` ->
    ``page_urls`` (see MangaBuilder), so the ``{number: reader_url}`` chapter map
    scraped from the series page is cached on the instance.
    """

    def __init__(self, manga_url: str, base_url: str = BASE_URL) -> None:
        super().__init__(manga_url, base_url)
        self._chapter_urls: Dict[str, str] = {}

    def _manga_page_url(self) -> str:
        return f"{self.base_url}/read-manga/{self.manga_url.replace(' ', '_')}"

    def all_volume_ids(self) -> Iterable[str]:
        """All chapter numbers for the manga, in canonical order.

        Scrapes the series page's ``table#chapter_table`` and caches the
        ``{number: reader_url}`` map so ``volume_url`` can turn a chapter number
        back into the url the reader page needs.
        """
        url = self._manga_page_url()
        logger.info(f"Manga url={url}")
        soup = self._fetch_manga_page(url, BrowserFetcher())
        self._chapter_urls = _chapter_map_from_soup(soup)
        if not self._chapter_urls:
            raise MangaDoesNotExist(
                f"No chapters found for {self.manga_url} (bad slug or page blocked)"
            )
        return sort_chapter_ids(self._chapter_urls.keys())

    def volume_url(self, volume: str) -> str:
        """Reader-page url for a chapter number (from the cached map)."""
        if not self._chapter_urls:
            self.all_volume_ids()
        url = self._chapter_urls.get(volume)
        if not url:
            raise VolumeDoesntExist(f"Chapter {volume} not found for {self.manga_url}")
        return url

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """Return ``[(page_number, image_url)]`` for every page in a chapter.

        The reader page embeds every page image as ``<img id="pageN">``, so a
        single browser fetch of the chapter url yields the whole list.
        """
        url = self.volume_url(volume)
        logger.info(f"Volume url={url}")
        soup = self._fetch_manga_page(url, BrowserFetcher())
        images = _image_urls_from_soup(soup)
        if not images:
            raise VolumeDoesntExist(
                f"No page images found for {self.manga_url} chapter {volume}"
            )
        return list(enumerate(images, start=1))


class MangagoSearch(BaseSearchParser):
    """
    Parses search queries on mangago.me.

    Search results render as ``div.row-1`` cards; the search action is JS/
    Cloudflare-gated, so ``base._scrape_results`` drives a real browser. This
    stage was unchanged by the C2 re-probe.
    """

    def __init__(self, query: str, base_url: str = BASE_URL) -> None:
        super().__init__(query, base_url)

    def _parse_search_result(self, result: Tag) -> SearchResult:
        """Extract one search result's metadata from its ``div.box`` card.

        The card holds a thumbnail link plus text rows: the title link in
        ``div.row-1`` and the latest chapters as ``a.chico`` in a later row. The
        first ``<a>`` in the card is the *thumbnail* (no text), so the title is
        read from the row-1 link specifically; the latest chapter is the first
        ``a.chico`` (text like "Vol.72 Ch.700.6").
        """
        row1 = result.find("div", {"class": "row-1"})
        title_link = row1.find("a") if isinstance(row1, Tag) else result.find("a")
        manga_title = text(title_link).strip()
        manga_url = attr(title_link, "href")
        manga_url_short = Path(manga_url).stem.split("/")[-1]
        chico = result.find("a", {"class": "chico"})
        if isinstance(chico, Tag):
            match = re.search(
                r"ch(?:apter)?\.?\s*([0-9]+(?:\.[0-9]+)?)", text(chico), re.I
            )
            latest_chapter = match.group(1) if match else text(chico).strip()
        else:
            latest_chapter = ""
        return SearchResult(
            title=manga_title,
            manga_url=manga_url_short,
            latest_chapter=latest_chapter,
            source="mangago",
        )

    def search(self, start: int = 1) -> SearchResults:
        url = f"{self.base_url}/r/l_search/?name={self.query.replace(' ', '+')}"
        logger.info(f"search_url={url}")
        # each result is a div.box card under ul#search_list (title in row-1,
        # latest chapters as a.chico in a sibling row) -- scope to the results
        # list so stray boxes elsewhere on the page aren't picked up.
        results = self._scrape_results(url, "#search_list div.box")
        metadata: SearchResults = {}
        for key, result in enumerate(results, start=start):
            metadata[str(key)] = self._parse_search_result(result)
        return metadata


@register_source("mangago")
class Mangago(BaseSiteParser):
    """Site parser for mangago.me."""

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=MangagoMangaParser,
            search_parser=MangagoSearch,
        )
