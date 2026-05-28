import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import requests  # type: ignore
from bs4 import BeautifulSoup
from bs4.element import Tag
import nodriver as nd

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.new_types import SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.utils import get_html_from_url

logger = logging.getLogger(__name__)


class MangafireMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on https://mangafire.to
    """

    def __init__(
        self, manga_url: str, base_url: str = "https://mangafire.to"
    ) -> None:
        super().__init__(manga_url, base_url)
        self.headers = {"Referer": self.base_url}

    def _scrape_volume(self, volume: str) -> BeautifulSoup:
        """
        Retrieve HTML for a given manga volume number
        """
        try:
            url = self.volume_url(volume)
            logger.info(f"Volume url={url}")
            volume_html = get_html_from_url(url, "nodriver")
            return volume_html
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} volume {volume} does not exist")
                raise VolumeDoesntExist(
                    f"Manga {self.manga_url} volume {volume} does not exist"
                )
        return None

    def volume_url(self, volume: str) -> str:
        return f"{self.base_url}/read/{self.manga_url}/en/chapter-{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return a list of urls for every page in a given volume
        """
        volume_html = self._scrape_volume(volume)
        if volume_html:
            # Use nodriver to scroll and load images
            import nodriver as nd
            import os
            from pathlib import Path

            async def load_images():
                profile_path = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
                browser = await nd.start(user_data_dir=profile_path, headless=True, sandbox=False, no_sandbox=True)
                page = await browser.get(self.volume_url(volume))
                await page.wait(5)

                # Scroll the page to load all lazy images
                for _ in range(50):  # scroll multiple times to ensure all images load
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                    await page.wait(1)

                await page.wait(3)
                images = await page.query_selector_all('main img')
                loaded_urls = []
                for img in images:
                    src = img.attrs.get('src') or img.attrs.get('data-src')
                    if src and 'mfcdn' in src:
                        loaded_urls.append(src)
                browser.stop()
                return loaded_urls

            image_urls = asyncio.run(load_images())
            return list(enumerate(image_urls, start=1))
        return None

    def all_volume_ids(self) -> Iterable[str]:
        """
        Get the list of all volume numbers for a manga
        """
        try:
            url = f"{self.base_url}/manga/{self.manga_url}"
            logger.info(f"Manga url={url}")
            manga_html = get_html_from_url(url, "requests")
            anchors = manga_html.find_all("a", href=re.compile(r"/read/.*chapter-\d+"))
            volume_ids = set()
            for a in anchors:
                href = a.get("href")
                match = re.search(r"chapter-(\d+)", href)
                if match:
                    volume_ids.add(match.group(1))
            return sorted(volume_ids, key=int)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.warning(f"Manga {self.manga_url} does not exist")
                raise MangaDoesNotExist(f"Manga {self.manga_url} does not exist")
            raise e


class MangafireSearch(BaseSearchParser):
    """
    Parses search queries
    """

    def __init__(self, query: str, base_url: str = "https://mangafire.to") -> None:
        super().__init__(query, base_url)

    def search(self, start: int = 1) -> SearchResults:
        """
        Extract each mangas metadata from the search results
        Note: Search filtering is currently not available due to Cloudflare captcha protection.
        """
        logger.warning("Mangafire search filtering is not available due to Cloudflare captcha protection. Please use direct manga URLs instead.")
        # Return empty results for now
        return {}


class Mangafire(BaseSiteParser):
    """
    Scraper & parser for mangafire.to
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://mangafire.to",
            manga_parser=MangafireMangaParser,
            search_parser=MangafireSearch,
        )