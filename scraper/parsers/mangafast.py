import logging
import re
from typing import Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import fetch_soup
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr, text
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source
from scraper.selection import ChapterId

logger = logging.getLogger(__name__)


class MangaFastMangaParser(BaseMangaParser):
    def __init__(self, manga_url: str, base_url: str = "http://mangafast.net") -> None:
        super().__init__(manga_url, base_url)

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        highest_volume = next(iter(self.all_volume_ids()))
        if int(volume) > int(highest_volume):
            raise VolumeDoesntExist(f"Manga volume {volume} does not exist")
        try:
            volume_html = fetch_soup(self.volume_url(volume))
            return volume_html
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise MangaDoesNotExist(
                    f"Manga {self.manga_url} or volume {volume} does not exist"
                )
            raise e

    def volume_url(self, volume: str) -> str:
        return f"{self.base_url}/{self.manga_url}-chapter-{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        volume_html = self._scrape_volume(volume)
        img_tags = volume_html.find("div", id="Read").find_all("img")  # type: ignore[union-attr]
        img_urls: List[Tuple[int, str]] = []
        for page_num, url_tag in enumerate(img_tags, 1):
            url = attr(url_tag, "data-src")
            if page_num == 1:
                url = attr(url_tag, "src")
            img_urls.append((page_num, url))
        return img_urls

    def all_volume_ids(self) -> Iterable[str]:
        try:
            url = f"{self.base_url}/{self.manga_url}?order=old#table"
            manga_html = fetch_soup(url)
            volume_tags = manga_html.find("table", id="table").find_all("a")  # type: ignore[attr-defined]
            volume_tags = [tag for tag in volume_tags if tag.text != "PDF"]
            volume_ids = [re.sub(r"\D", "", x.text.strip()) for x in volume_tags]
            # The list is scraped newest-first; keep that order but drop any
            # entries above the latest chapter. Compare by chapter number, not
            # lexicographically ("9" must not be treated as > "62").
            highest_volume = ChapterId(volume_ids[0])
            return [vol for vol in volume_ids if ChapterId(vol) <= highest_volume]
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangaFastSearch(BaseSearchParser):
    def __init__(self, query: str, base_url: str = "https://mangafast.net") -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> SearchResult:
        title = text(result.find("h3")).strip()
        manga_url = attr(result.find("a"), "href")
        chapters = text(result.find("b"))
        return SearchResult(
            title=title,
            manga_url=manga_url.split("/")[-2],
            chapters=re.sub(r"\D", "", chapters),
            source="mangafast",
        )

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        """
        url = f"{self.base_url}/?s={self.query}"
        results = self._scrape_results(url, div_class="ls5")
        metadata: SearchResults = {}
        for key, result in enumerate(results, start=start):
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        return metadata


@register_source("mangafast")
class MangaFast(BaseSiteParser):
    """
    Scraper & parser for mangareader.net
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="http://mangafast.net",
            manga_parser=MangaFastMangaParser,
            search_parser=MangaFastSearch,
        )
