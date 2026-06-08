"""
Scraper & parser for natomanga.com (kakalot-family engine).

FRED: search fails because of cloudflare
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url:
https://www.natomanga.com/search/story/billy_bat
"""

import logging
from typing import Optional

from scraper.parsers.base import BaseSiteParser
from scraper.parsers.kakalot import KakalotMangaParser, KakalotSearchParser
from scraper.registry import register_source

logger = logging.getLogger(__name__)

BASE_URL = "https://natomanga.com"


class ManganatoMangaParser(KakalotMangaParser):
    base_url = BASE_URL
    manga_path = "{base_url}/manga/{slug}"
    page_img_attr = "src"


class ManganatoSearch(KakalotSearchParser):
    base_url = BASE_URL
    source = "manganato"


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
