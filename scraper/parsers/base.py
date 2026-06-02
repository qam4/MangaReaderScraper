"""
Abstract base classes for all parsers
"""

import abc
import io
import logging
import sys
import time
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple, Type

import requests  # type: ignore
from bs4.element import Tag
from PIL import Image, ImageDraw, ImageFont

from scraper.exceptions import MangaParserNotSet  # , PageDoesNotExist
from scraper.fetchers import BrowserFetcher, fetch_soup
from scraper.new_types import SearchResults
from scraper.utils import request_session

logger = logging.getLogger(__name__)


class BaseMangaParser:
    """
    Parses data associated with a given manga
    """

    def __init__(self, manga_url: str, base_url: str = "") -> None:
        self.manga_url = manga_url
        self.base_url = base_url
        self.headers: dict[str, str] = {}

    @abc.abstractmethod
    def volume_url(self, volume: str) -> str:
        """
        Return the url of a volume
        """
        pass

    @abc.abstractmethod
    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of tuples [page_number, urls] for every page in a given volume
        """
        pass

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """
        Extracts a manga pages data
        """
        attempt = 0
        MAX_TRIES = 5
        BACKOFF_SECONDS = 1
        page_num, img_url = page_url
        req = None

        while attempt < MAX_TRIES:
            try:
                with request_session() as session:
                    req = session.get(img_url, headers=self.headers, timeout=30)
                if req.status_code == 200:
                    break
                logger.warning(
                    f"Attempt {attempt + 1}/{MAX_TRIES} failed for page {page_num} with status {req.status_code}: {img_url}"
                )
            except requests.exceptions.RequestException as err:
                logger.warning(
                    f"Attempt {attempt + 1}/{MAX_TRIES} failed for page {page_num}: {img_url} - {err}"
                )
            attempt += 1
            time.sleep(BACKOFF_SECONDS * attempt)

        if not req or req.status_code != 200:
            logger.error(f"Download FAILED page {page_num} at {img_url}")
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{img_url}"),
                "missing",
            )
        img_data = req.content

        # check image
        try:
            img = Image.open(io.BytesIO(img_data))
            img.verify()
            img = Image.open(io.BytesIO(img_data))
            img.load()
        except Exception as err:
            logger.error(
                f"Image file page {page_num} at {img_url} corrupted. Error: {str(err)}"
            )
            return (
                int(page_num),
                self.create_page(f"Page {page_num} corrupted.\n{str(err)}\n{img_url}"),
                "corrupted",
            )

        return (int(page_num), img_data, "success")

    def create_page(self, text: str) -> bytes:
        # Create a new image
        image = Image.new("RGB", (1000, 1500), color=(73, 109, 137))

        # Create a drawing object
        draw = ImageDraw.Draw(image)

        # Choose a font
        font = ImageFont.truetype("arial.ttf", 20)

        # Add text to the image
        draw.text((50, 70), text, font=font, fill=(255, 255, 255))

        # Save the image as a JPG file
        stream = io.BytesIO()
        image.save(stream, "JPEG")
        return stream.getvalue()

    @abc.abstractmethod
    def all_volume_ids(self) -> Iterable[str]:
        """
        All volume identifiers for a manga (used to create the volume url)
        """
        pass


class BaseSearchParser:
    """
    Parse search queries & returns the results
    """

    def __init__(self, query: str, base_url: str = "") -> None:
        self.query: str = query
        self.base_url: str = base_url

    def _scrape_results(self, url: str, div_class: str) -> List[Tag]:
        """
        Scrape and return HTML list with search results
        """
        # search results are dynamically loaded, so drive a real browser
        html_response = fetch_soup(url, BrowserFetcher())
        # logging.debug(f"html_response={html_response}")
        search_results = html_response.find_all("div", {"class": div_class})
        if not search_results:
            logging.error(f"No search results found for {self.query}\nExiting...")
            sys.exit()
        self.results = search_results
        # logging.debug(f"search_results={search_results}")
        return search_results  # type: ignore[return-value]

    @abc.abstractmethod
    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        """
        pass


class BaseSiteParser:
    """
    Base parser for a specific manga website
    """

    __metaclass__ = abc.ABCMeta

    def __init__(
        self,
        base_url: str,
        manga_parser: Type[BaseMangaParser],
        search_parser: Type[BaseSearchParser],
        manga_url: Optional[str],
    ):
        logger.debug(f"[BaseSiteParser] base_url={base_url}, manga_url={manga_url}")
        self.base_url = base_url
        self._manga_parser = manga_parser
        self._search_parser = search_parser
        self._manga: Optional[BaseMangaParser] = (
            None if not manga_url else self._manga_parser(manga_url)
        )

    def __new__(cls, *args, **kwargs) -> "BaseSiteParser":
        if cls is BaseSiteParser:
            raise Exception("Abstract class cannot be instantiatied")
        return object.__new__(cls)

    @property
    def manga(self) -> BaseMangaParser:
        if self._manga:
            return self._manga
        raise MangaParserNotSet("No parser has been set")

    @manga.setter
    def manga(self, manga_url: str) -> None:
        self._manga = self._manga_parser(manga_url)

    @lru_cache()
    def search(self, query: str) -> SearchResults:
        search_parser = self._search_parser(query)
        return search_parser.search()
