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


class MangagoMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on https://www.mangago.me
    CLOUDFLARE protected
    """

    def __init__(
        self, manga_url: str, base_url: str = "https://www.mangago.me"
    ) -> None:
        super().__init__(manga_url, base_url)

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        """
        Retrieve HTML for a given manga volume number
        """
        try:
            url = self.volume_url(volume)
            logger.info(f"Volume url={url}")
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
        return f"{self.base_url}/read-manga/{self.manga_url}/{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        Fred: mangago only shows one page at a time, so we need to first
        get a lit of page urls from the volume page, then scrape each page url to get the image url
        """
        volume_html = self._scrape_volume(volume)
        logger.info(f"volume_html={volume_html}")
        if volume_html:
            container = volume_html.find("div", {"q:key": "zn_2"})
            logger.info(f"container={container}")

            all_img_tags = container.find_all("img")  # type: ignore[attr-defined]
            logger.info(f"all_img_tags[0]={all_img_tags[0]}")
            all_page_urls = [img.get("src") for img in all_img_tags]
            return list(enumerate(all_page_urls, start=1))
        return None

    def _extract_number(self, vol_tag: Tag) -> str:
        """
        Sanitises a number from scraped chapter tag
        """
        logger.info(f"vol_tag={vol_tag}")
        base = f"{self.base_url}/read-manga/{self.manga_url}/"
        if vol_tag.startswith(base):
            vol_text = vol_tag[len(base) :].rstrip("/")
        else:
            raise ValueError(f"vol_tag does not start with expected base: {base}")
        logger.info(f"vol_text={vol_text}")
        return vol_text

    def all_volume_ids(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            url = f"{self.base_url}/read-manga/{self.manga_url.replace(' ', '_')}"
            logger.info(f"Manga url={url}")
            manga_html = fetch_soup(url, BrowserFetcher())
            # logger.info(f"manga_html={manga_html}")

            # container = manga_html.find("table", {"class": "listing"})
            # logger.info(f"container={container}")

            # volume_tags = container.find_all("a", {"class": "chico"})
            # logger.info(f"volume_tags[0]={volume_tags[0]}, len={len(volume_tags)}")
            # # <a class="chico" href="https://www.mangago.me/read-manga/boku_no_hero_academia/bt/br_chapter-273310/pg-1/">
            # volume_ids = list(
            #     reversed(
            #         list(
            #             dict.fromkeys(
            #                 self._extract_number(vol.get("href"))  # type: ignore[union-attr]
            #                 for vol in volume_tags
            #             )
            #         )
            #     )
            # )
            volume_tags = manga_html.find_all(
                "table", {"class": "listing", "id": "chapter_table"}
            )
            # logger.info(volume_tags)
            volume_ids = set(
                self._extract_number(vol.find("a").get("href")) for vol in volume_tags  # type: ignore[union-attr]
            )
            logger.info(f"volume_ids={volume_ids}")
            return volume_ids
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangagoSearch(BaseSearchParser):
    """
    Parses search queries
    """

    def __init__(self, query: str, base_url: str = "https://www.mangago.me") -> None:
        super().__init__(query, base_url)

    def _extract_text(self, result: Tag) -> Dict[str, str]:
        """
        Extract the desired text from a HTML search result
        """
        manga_title = result.find("a").text.strip()
        logger.info(f"manga_title={manga_title}")
        manga_url = result.find("a").get("href")  # type: ignore[attr-defined]
        logger.info(f"manga_url={manga_url}")
        manga_url_short = Path(manga_url).stem.split("/")[-1]
        # fred: last capters are in "row-5 gray"
        last_chapter = result.find("a", {"class": "chico"})
        logger.info(f"last_chapter={last_chapter}")
        if last_chapter:
            chapters = last_chapter.find("span").text  # type: ignore[attr-defined]
        else:
            chapters = 0
        logger.info(f"chapters={chapters}")
        return {
            "title": manga_title,
            "manga_url": manga_url_short,
            "chapters": chapters,
            "source": "mangago",
        }

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results

        FRED: search fails because of cloudflare
        requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://www.mangago.me/r/l_search/?name=billy+bat

        4/12/2026: search works with selenium NOT headless.
        """
        url = f"{self.base_url}/r/l_search/?name={self.query.replace(' ', '+')}"
        logger.info(f"search_url={url}")
        results = self._scrape_results(url, div_class="row-1")
        metadata = {}
        for key, result in enumerate(results, start=start):
            logger.info(f"key={key}, result={result}")
            manga_metadata = self._extract_text(result)
            metadata[str(key)] = manga_metadata
        return metadata


class Mangago(BaseSiteParser):
    """
    Seems to be the same as mangago.com

    Can probably use this class for mangago too
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://www.mangago.me",
            manga_parser=MangagoMangaParser,
            search_parser=MangagoSearch,
        )
