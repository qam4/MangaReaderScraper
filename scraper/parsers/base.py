"""
Abstract base classes for all parsers
"""

import abc
import io
import logging
import time
from functools import lru_cache
from typing import Iterable, List, Optional, Tuple, Type

import requests  # type: ignore
from bs4.element import Tag
from PIL import Image, ImageDraw, ImageFont

from scraper.exceptions import (  # , PageDoesNotExist
    MangaParserNotSet,
    NoSearchResultsFound,
)
from scraper.fetchers import BrowserFetcher, Fetcher, fetch_soup
from scraper.new_types import SearchResults
from scraper.utils import request_session

logger = logging.getLogger(__name__)


def _placeholder_font(size: int):
    """Font for placeholder ("page missing/corrupted") images.

    Prefer arial.ttf when present; fall back to Pillow's always-available
    bundled font. This matters because ``create_page`` runs on the
    download-failure path -- it must never raise just because a TTF is absent
    (arial.ttf is Windows-only; Linux/macOS/CI don't have it).
    """
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


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

        # Choose a font. create_page runs on the download-FAILURE path, and
        # arial.ttf isn't present on every platform (Linux/macOS/CI), so fall
        # back to Pillow's bundled default rather than letting the error handler
        # itself raise OSError.
        font = _placeholder_font(20)

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

    def author(self) -> Optional[str]:
        """
        The manga's author(s), for ComicInfo <Writer>, or None when the site
        doesn't expose one. Default is None; parsers override where the site
        carries an author (e.g. MangaFire's series page). Multiple authors should
        be returned as a single comma-separated string.
        """
        return None


class BaseSearchParser:
    """
    Parse search queries & returns the results
    """

    def __init__(self, query: str, base_url: str = "") -> None:
        self.query: str = query
        self.base_url: str = base_url

    def _scrape_results(
        self,
        url: str,
        selector: str,
        fetcher: Optional[Fetcher] = None,
    ) -> List[Tag]:
        """
        Fetch the search page and return the result-element list.

        ``selector`` is a CSS selector for the result container, so results in
        ANY tag work (``li.result``, ``tr``, ``article``, ``div.box`` ...) --
        search results are not always ``<div>``s. ``fetcher`` overrides the
        default browser fetch for sites whose search is reachable with a cheaper
        client (pass e.g. ``CurlCffiFetcher()`` when ``api_backends.txt`` says so);
        defaults to a real browser for the Cloudflare/JS-gated case.
        """
        html_response = fetch_soup(url, fetcher or BrowserFetcher())
        search_results = html_response.select(selector)
        if not search_results:
            raise NoSearchResultsFound(f"No search results found for {self.query}")
        self.results = search_results
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
