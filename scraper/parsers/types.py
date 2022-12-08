from typing import Type, Union

from scraper.parsers.mangafast import MangaFast, MangaFastMangaParser, MangaFastSearch
from scraper.parsers.mangakaka import MangaKaka, MangaKakaMangaParser, MangaKakaSearch
from scraper.parsers.manganelo import Manganelo, ManganeloMangaParser, ManganeloSearch
from scraper.parsers.mangareader import (
    MangaReader,
    MangaReaderMangaParser,
    MangaReaderSearch,
)

MangaParser = Union[
    MangaReaderMangaParser,
    MangaKakaMangaParser,
    ManganeloMangaParser,
    MangaFastMangaParser,
]
SearchParser = Union[
    MangaReaderSearch, MangaKakaSearch, ManganeloSearch, MangaFastSearch
]
SiteParser = Union[MangaReader, MangaKaka, Manganelo, MangaFast]


MangaParserClass = Union[
    Type[MangaReaderMangaParser],
    Type[MangaKakaMangaParser],
    Type[ManganeloMangaParser],
    Type[MangaFastMangaParser],
]
SearchParserClass = Union[
    Type[MangaReaderSearch],
    Type[MangaKakaSearch],
    Type[ManganeloSearch],
    Type[MangaFastSearch],
]
SiteParserClass = Union[
    Type[MangaReader], Type[MangaKaka], Type[Manganelo], Type[MangaFast]
]
