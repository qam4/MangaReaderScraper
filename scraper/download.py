"""
Downloads manga page images
"""

import logging
from logging import LoggerAdapter
from typing import List, Optional, Type

from scraper.manga import Manga, MangaBuilder
from scraper.parsers.types import SiteParser
from scraper.utils import download_timer, get_adapter

logger = logging.getLogger(__name__)


class Download:
    """
    Downloads the manga in the desired format
    """

    def __init__(self, manga_url: str, filetype: str, parser: Type[SiteParser]) -> None:
        self.manga_url: str = manga_url
        self.factory: MangaBuilder = MangaBuilder(
            parser=parser(manga_url), filetype=filetype
        )
        self.adapter: LoggerAdapter = get_adapter(logger, manga_url)
        self.type: str = filetype

    @download_timer
    def download_volumes(
        self,
        vol_nums: Optional[List[str]] = None,
        title: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> Manga:
        """
        Download all pages and volumes of a manga
        """
        self.adapter.info("Starting Downloads")
        manga = self.factory.get_manga_volumes(vol_nums, title, preferred_name)
        if not manga.volumes:
            return manga
        self.adapter.info("All volumes downloaded")
        return manga
