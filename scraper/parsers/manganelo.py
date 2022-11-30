import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.new_types import SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.utils import get_html_from_url

logger = logging.getLogger(__name__)


class ManganeloMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on mangakakalot.com

    WARNING: this no longer works due to mangakakalot integrating
             cloudflare blocking

             Note: https://mangakakalot.com,
                   https://readmanganato.com (protected from download),
                   https://manganelo.tv
    """

    def __init__(
        self, manga_name: str, base_url: str = "https://manganelo.tv"
    ) -> None:
        super().__init__(manga_name, base_url)

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        """
        Retrieve HTML for a given manga volume number
        """
        try:
            # [manganelo.com]
            url = f"{self.base_url}/chapter/manga-{self.name}/chapter-{volume}"
            # [readmanganato.com]url = f"{self.base_url}/manga-{self.name}/chapter-{volume}"
            logger.info(f'Volume url={url}')
            volume_html = get_html_from_url(url)
            string = re.compile("404 NOT FOUND")
            matches = volume_html.find_all(string=string, recursive=True)
            if matches:
                raise VolumeDoesntExist(f"Manga {self.name} volume {volume} does not exist {volume_html}")
            return volume_html
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.info(f"Manga {self.name} volume {volume} does not exist")
                return None

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        """
        volume_html = self._scrape_volume(volume)
        if volume_html:
            container = volume_html.find("div", {"class": "container-chapter-reader"})
            all_img_tags = container.find_all("img")
            # logger.info(f"all_img_tags[0]={all_img_tags[0]}")
            # [readmanganato.com]all_page_urls = [img.get("src") for img in all_img_tags]
            # [manganelo.com]
            all_page_urls = [img.get("data-src") for img in all_img_tags]
            return enumerate(all_page_urls, start=1)
        return None

    def _extract_number(self, vol_tag: Tag) -> str:
        """
        Sanitises a number from scraped chapter tag
        """
        vol_text = vol_tag.split("/")[-1].split("-")[-1]
        return vol_text

    def all_volume_numbers(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            # [readmanganato.com]url = f"{self.base_url}/manga-{self.name}"
            # [manganelo.com]
            url = f"{self.base_url}/manga/manga-{self.name}"
            logger.info(f"Manga url={url}")
            manga_html = get_html_from_url(url)
            # logger.info(f"[all_volume_numbers] manga_html={manga_html}")

            volume_tags = manga_html.find_all("li", {"class": "a-h"})
            # logger.info(volume_tags)
            volume_numbers = set(
                self._extract_number(vol.find("a").get("href")) for vol in volume_tags
            )
            # logger.info(f'volume_numbers={volume_numbers}')
            return volume_numbers
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.name} does not exist")
            raise e


class ManganeloSearch(BaseSearchParser):
    """
    Parses search queries from manganelo.tv
    """

    def __init__(self, query: str, base_url: str = "https://manganelo.tv") -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> Dict[str, str]:
        """
        Extract the desired text from a HTML search result
        """
        manga_name = result.find("img").get("alt")
        # logger.info(f"manga_name={manga_name}")
        manga_url = result.find("a").get("href")
        # logger.info(f"manga_url={manga_url}")
        manga_url_short = Path(manga_url).stem.split('-')[-1]
        last_chapter = result.find("a", {"class": "item-chapter a-h text-nowrap"})
        # [mangakakalot.com]last_chapter = result.find("em", {"class": "story_chapter"}).find("a", {"rel": "nofollow"})
        # logger.info(f"last_chapter={last_chapter}")
        chapters = last_chapter.get("href").split("-")[-1]
        # logger.info(f"chapters={chapters}")
        return {
            "title": manga_name,
            "manga_url": manga_url_short,
            "chapters": chapters,
            "source": "manganelo",
        }

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        """
        # [manganelo.com]
        url = f"{self.base_url}/search/{self.query.replace(' ', '%20')}"
        # [manganato.com]url = f"{self.base_url}/search/story/{self.query.replace(' ', '_')}"
        logger.info(f"search_url={url}")
        results = self._scrape_results(url, div_class="search-story-item")
        # [mangakakalot.com]results = self._scrape_results(url, div_class="story_item")
        metadata = {}
        for key, result in enumerate(results, start=start):
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        # logger.info(f'metadata={metadata}')
        return metadata


class Manganelo(BaseSiteParser):
    """
    Seems to be the same as manganelo.com

    Can probably use this class for manganelo too
    """

    def __init__(self, manga_name: Optional[str] = None) -> None:
        super().__init__(
            manga_name=manga_name,
            base_url="https://manganelo.tv",
            manga_parser=ManganeloMangaParser,
            search_parser=ManganeloSearch,
        )
