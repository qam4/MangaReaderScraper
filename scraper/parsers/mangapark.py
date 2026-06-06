import logging
import re
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import BrowserFetcher, fetch_soup
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers._html import attr, text
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source

logger = logging.getLogger(__name__)


class MangaparkMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on mangapark.io

    WARNING: the volume page containing the images urls is using javascript.
            needs delay before getting the scripts loaded.
    """

    def __init__(self, manga_url: str, base_url: str = "https://mangapark.io") -> None:
        super().__init__(manga_url, base_url)

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        """
        Retrieve HTML for a given manga volume number
        """
        try:
            url = self.volume_url(volume)
            logger.debug(f"Volume url={url}")
            volume_html = fetch_soup(url, BrowserFetcher())
            string = re.compile("404 NOT FOUND")
            matches = volume_html.find_all(string=string, recursive=True)
            if matches:
                raise VolumeDoesntExist(
                    f"Manga {self.manga_url} volume {volume} does not exist {volume_html}"
                )
            return volume_html
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} volume {volume} does not exist")
                raise MangaDoesNotExist(
                    f"Manga {self.manga_url} volume {volume} does not exist"
                )
        return None

    def volume_url(self, volume: str) -> str:
        return f"{self.base_url}/title/{self.manga_url}/{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        """
        volume_html = self._scrape_volume(volume)
        if volume_html:
            items = volume_html.find_all("div", {"data-name": "image-item"})
            all_img_tags = [item.find("img") for item in items]  # type: ignore[union-attr]
            all_page_urls = [attr(img, "src") for img in all_img_tags]
            return list(enumerate(all_page_urls, start=1))
        return None

    def _extract_number(self, href: str) -> str:
        """Sanitise a chapter number from a chapter href (the last path part)."""
        return href.split("/")[-1]

    def all_volume_ids(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            url = f"{self.base_url}/title/{self.manga_url}"
            logger.debug(f"Manga url={url}")
            manga_html = fetch_soup(url)

            volume_tags = manga_html.find_all("div", {"q:key": "8t_8"})
            volume_ids = list(
                reversed(
                    list(
                        dict.fromkeys(
                            self._extract_number(attr(vol.find("a"), "href"))
                            for vol in volume_tags
                        )
                    )
                )
            )
            return volume_ids
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangaparkSearch(BaseSearchParser):
    """
    Parses search queries
    """

    def __init__(self, query: str, base_url: str = "https://mangapark.io") -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> SearchResult:
        """
        Extract the desired text from a HTML search result
        """
        manga_title = attr(result.find("img"), "alt")
        manga_url = attr(result.find("a"), "href")
        manga_url_short = Path(manga_url).stem.split("/")[-1]
        last_chapter = result.find(
            "a", {"class": "link-hover link-primary visited:link-accent"}
        )
        if last_chapter:
            chapters = text(last_chapter.find("span"))  # type: ignore[arg-type]
        else:
            chapters = ""
        return SearchResult(
            title=manga_title,
            manga_url=manga_url_short,
            chapters=chapters,
            source="mangapark",
        )

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        """
        url = f"{self.base_url}/search?word={self.query.replace(' ', '%20')}"
        logger.debug(f"search_url={url}")
        results = self._scrape_results(
            url, div_class="flex border-b border-b-base-200 pb-5"
        )
        metadata = {}
        for key, result in enumerate(results, start=start):
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        return metadata


@register_source("mangapark")
class Mangapark(BaseSiteParser):
    """
    Seems to be the same as mangapark.com

    Can probably use this class for mangapark too
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://mangapark.io",
            manga_parser=MangaparkMangaParser,
            search_parser=MangaparkSearch,
        )
