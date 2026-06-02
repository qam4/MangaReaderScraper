"""
Scraper & parser for nelomanga.net (kakalot-family engine).

FRED: search fails because of cloudflare
403 Client Error: Forbidden for url:
https://www.nelomanga.net/search/story/billy_bat
"""

import logging
from typing import Optional

from scraper.parsers.base import BaseSiteParser
from scraper.parsers.kakalot import KakalotMangaParser, KakalotSearchParser
from scraper.registry import register_source

logger = logging.getLogger(__name__)

BASE_URL = "https://nelomanga.net"


class ManganeloMangaParser(KakalotMangaParser):
    base_url = BASE_URL
    volume_path = "{base_url}/chapter/manga-{slug}/chapter-{volume}"
    manga_path = "{base_url}/manga/manga-{slug}"
    page_img_attr = "data-src"
    chapter_href_sep = "-"


class ManganeloSearch(KakalotSearchParser):
    base_url = BASE_URL
    source = "manganelo"
    result_div_class = "story_item"


@register_source("manganelo")
class Manganelo(BaseSiteParser):
    """Scraper & parser for nelomanga.net."""

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=ManganeloMangaParser,
            search_parser=ManganeloSearch,
        )
