"""
Scraper & parser for mangakakalot.gg (kakalot-family engine).

FRED: search fails because of cloudflare
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url:
https://www.mangakakalot.gg/search/story/billy_bat
"""

import logging
from typing import Optional

from scraper.parsers.base import BaseSiteParser
from scraper.parsers.kakalot import KakalotMangaParser, KakalotSearchParser
from scraper.registry import register_source

logger = logging.getLogger(__name__)

BASE_URL = "https://mangakakalot.gg"


class MangaKakaMangaParser(KakalotMangaParser):
    base_url = BASE_URL
    manga_path = "{base_url}/manga/{slug}"
    page_img_attr = "src"


class MangaKakaSearch(KakalotSearchParser):
    base_url = BASE_URL
    source = "mangakaka"


@register_source("mangakaka")
class MangaKaka(BaseSiteParser):
    """Scraper & parser for mangakakalot.gg."""

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=MangaKakaMangaParser,
            search_parser=MangaKakaSearch,
        )
