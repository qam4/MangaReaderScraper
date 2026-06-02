"""
Scraper & parser for natomanga.com (kakalot-family engine).

FRED: search fails because of cloudflare
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url:
https://www.natomanga.com/search/story/billy_bat
"""

import logging
from pathlib import Path
from typing import Optional

from bs4.element import Tag

from scraper.parsers.base import BaseSiteParser
from scraper.parsers.kakalot import KakalotMangaParser, KakalotSearchParser
from scraper.registry import register_source

logger = logging.getLogger(__name__)

BASE_URL = "https://natomanga.com"


class ManganatoMangaParser(KakalotMangaParser):
    base_url = BASE_URL
    volume_path = "{base_url}/manga-{slug}/chapter-{volume}"
    manga_path = "{base_url}/manga-{slug}"
    page_img_attr = "src"
    chapter_href_sep = "-"


class ManganatoSearch(KakalotSearchParser):
    base_url = BASE_URL
    source = "manganato"
    result_div_class = "search-story-item"

    def _slug(self, result: Tag) -> str:
        manga_url = result.find("a").get("href")  # type: ignore[union-attr]
        return Path(manga_url).stem.split("-")[-1]

    def _chapters(self, result: Tag) -> str:
        last = result.find("a", {"class": "item-chapter a-h text-nowrap"})
        if not last:
            return ""
        return last.get("href").split("-")[-1]  # type: ignore[union-attr]


@register_source("manganato")
class Manganato(BaseSiteParser):
    """Scraper & parser for natomanga.com."""

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=ManganatoMangaParser,
            search_parser=ManganatoSearch,
        )
