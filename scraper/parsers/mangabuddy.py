import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.fetchers import BrowserFetcher, fetch_soup
from scraper.new_types import SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser

logger = logging.getLogger(__name__)


class MangabuddyMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on https://www.mangabuddy.com
    Images are CLOUDFLARE protected, bypassing using headers = {"Referer": "https://mangabuddy.com/"}
    """

    def __init__(
        self, manga_url: str, base_url: str = "https://www.mangabuddy.com"
    ) -> None:
        super().__init__(manga_url, base_url)
        self.headers = {"Referer": "https://mangabuddy.com/"}

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
        return f"{self.base_url}/{self.manga_url}/{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        """
        volume_html = self._scrape_volume(volume)
        logger.debug(f"volume_html={volume_html}")
        if volume_html:
            items = volume_html.find_all("div", {"class": "chapter-image"})
            logger.debug(f"items={items}")
            all_img_tags = [item.find("img") for item in items]  # type: ignore[union-attr]
            logger.debug(f"all_img_tags={all_img_tags}")
            all_page_urls = [img.get("src") for img in all_img_tags]  # type: ignore[union-attr]
            return list(enumerate(all_page_urls, start=1))
        return None

    def _extract_number(self, vol_tag: Tag) -> str:
        """
        Sanitises a number from scraped chapter tag
        """
        vol_text = vol_tag.split("/")[-1]
        return vol_text

    def all_volume_ids(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            url = f"{self.base_url}/{self.manga_url.replace(' ', '_')}"
            logger.debug(f"Manga url={url}")
            manga_html = fetch_soup(url)
            # logger.debug(f"manga_html={manga_html}")

            container = manga_html.find("ul", {"class": "chapter-list"})
            # logger.debug(f"container={container}")

            volume_tags = container.find_all("li")  # type: ignore[attr-defined]
            # logger.debug(f"volume_tags={volume_tags}")
            volume_ids = list(
                reversed(
                    list(
                        dict.fromkeys(
                            self._extract_number(vol.find("a").get("href"))
                            for vol in volume_tags
                        )
                    )
                )
            )
            logger.debug(f"volume_ids={volume_ids}")
            return volume_ids
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangabuddySearch(BaseSearchParser):
    """
    Parses search queries
    """

    def __init__(
        self, query: str, base_url: str = "https://www.mangabuddy.com"
    ) -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> Dict[str, str]:
        """
        Extract the desired text from a HTML search result
        """
        logging.debug(f"result={result}")
        manga_title = result.find("img").get("alt")  # type: ignore[attr-defined]
        logger.debug(f"manga_title={manga_title}")
        manga_url = result.find("a").get("href")  # type: ignore[attr-defined]
        logger.debug(f"manga_url={manga_url}")
        manga_url_short = Path(manga_url).stem.split("/")[-1]
        last_chapter = result.find("span", {"class": "latest-chapter"})
        logger.debug(f"last_chapter={last_chapter}")
        if last_chapter:
            chapters = last_chapter.text
        else:
            chapters = 0
        logger.debug(f"chapters={chapters}")
        return {
            "title": manga_title,
            "manga_url": manga_url_short,
            "chapters": chapters,
            "source": "mangabuddy",
        }

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results

        FRED: search fails because of cloudflare
        """
        url = f"{self.base_url}/search/?q={self.query.replace(' ', '+')}"
        logger.debug(f"search_url={url}")
        results = self._scrape_results(url, div_class="book-item")
        metadata = {}
        for key, result in enumerate(results, start=start):
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        return metadata


class Mangabuddy(BaseSiteParser):
    """
    Seems to be the same as mangabuddy.com

    Can probably use this class for mangabuddy too
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://www.mangabuddy.com",
            manga_parser=MangabuddyMangaParser,
            search_parser=MangabuddySearch,
        )
