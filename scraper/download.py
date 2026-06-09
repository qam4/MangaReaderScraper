"""
Downloads manga page images
"""

import logging
from logging import LoggerAdapter
from typing import List, Optional

from scraper.manga import Manga, MangaBuilder
from scraper.parsers.types import SiteParserClass
from scraper.utils import download_timer, get_adapter

logger = logging.getLogger(__name__)


class Download:
    """
    Downloads the manga in the desired format
    """

    def __init__(
        self,
        manga_url: str,
        filetype: str,
        parser: SiteParserClass,
        jobs: Optional[int] = None,
    ) -> None:
        self.manga_url: str = manga_url
        self.factory: MangaBuilder = MangaBuilder(
            parser=parser(manga_url), filetype=filetype, jobs=jobs
        )
        self.adapter: LoggerAdapter = get_adapter(logger, manga_url)
        self.type: str = filetype

    @download_timer
    def download_chapters(
        self,
        chapter_ids: Optional[List[str]] = None,
        title: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> Manga:
        """
        Download all pages and chapters of a manga
        """
        self.adapter.info("Starting Downloads")
        manga = self.factory.get_manga_chapters(chapter_ids, title, preferred_name)
        if not manga.chapters:
            return manga
        self.adapter.info("All chapters downloaded")
        return manga
