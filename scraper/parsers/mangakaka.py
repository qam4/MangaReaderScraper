import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.new_types import SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.utils import get_html_from_url

logger = logging.getLogger(__name__)


class MangaKakaMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on mangakakalot.com

    WARNING: this no longer works due to mangakakalot integrating
             cloudflare blocking

             Note: https://mangakakalot.com,
                   https://manganato.com/ (protected from download),
    """

    def __init__(
        self, manga_url: str, base_url: str = "https://mangakakalot.com"
    ) -> None:
        super().__init__(manga_url, base_url)

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        """
        Retrieve HTML for a given manga volume number
        """
        try:
            url = f"{self.base_url}/chapter/{self.manga_url}/chapter_{volume}"
            # [manganato.com]url = f"{self.base_url}/manga-{self.manga_url}/chapter-{volume}"
            logger.debug(f"Volume url={url}")
            volume_html = get_html_from_url(url)
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
                # return None

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        """
        volume_html = self._scrape_volume(volume)
        if volume_html:
            container = volume_html.find("div", {"class": "container-chapter-reader"})
            all_img_tags = container.find_all("img")
            logger.debug(f"all_img_tags[0]={all_img_tags[0]}")
            all_page_urls = [img.get("src") for img in all_img_tags]
            # [manganato.com]all_page_urls = [img.get("src") for img in all_img_tags]
            return list(enumerate(all_page_urls, start=1))
        return None

    def _extract_number(self, vol_tag: Tag) -> str:
        """
        Sanitises a number from scraped chapter tag
        """
        vol_text = vol_tag.split("/")[-1].split("_")[-1]
        return vol_text

    def all_volume_numbers(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            url = f"{self.base_url}/manga/{self.manga_url}"
            # [manganato.com]url = f"{self.base_url}/manga-{self.manga_url}"
            logger.info(f"Manga url={url}")
            manga_html = get_html_from_url(url)
            logger.debug(f"[all_volume_numbers] manga_html={manga_html}")

            volume_tags = manga_html.find_all("li", {"class": "a-h"})
            logger.debug(volume_tags)
            volume_numbers = set(
                self._extract_number(vol.find("a").get("href")) for vol in volume_tags
            )
            logger.debug(f"volume_numbers={volume_numbers}")
            return volume_numbers
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangaKakaSearch(BaseSearchParser):
    """
    Parses search queries from mangakakalot
    """

    def __init__(self, query: str, base_url: str = "https://mangakakalot.com") -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> Dict[str, str]:
        """
        Extract the desired text from a HTML search result
        """
        manga_title = result.find("img").get("alt")
        logger.debug(f"manga_title={manga_title}")
        manga_url = result.find("a").get("href")
        logger.debug(f"manga_url={manga_url}")
        last_chapter = result.find("em", {"class": "story_chapter"}).find("a")
        logger.debug(f"last_chapter={last_chapter}")
        chapters = last_chapter.get("href").split("_")[-1]
        logger.debug(f"chapters={chapters}")
        return {
            "title": manga_title,
            "manga_url": Path(manga_url).stem,
            "chapters": chapters,
            "source": "mangakaka",
        }

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        """
        url = f"{self.base_url}/search/story/{self.query.replace(' ', '_')}"
        logger.debug(f"search_url={url}")
        results = self._scrape_results(url, div_class="story_item")
        metadata = {}
        for key, result in enumerate(results, start=start):
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        return metadata


class MangaKaka(BaseSiteParser):
    """
    Seems to be the same as manganelo.com

    Can probably use this class for manganelo too
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://mangakakalot.com",
            manga_parser=MangaKakaMangaParser,
            search_parser=MangaKakaSearch,
        )
