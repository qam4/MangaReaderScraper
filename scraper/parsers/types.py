from typing import Type, Union

from scraper.parsers.mangafast import MangaFast, MangaFastMangaParser, MangaFastSearch
from scraper.parsers.mangakaka import MangaKaka, MangaKakaMangaParser, MangaKakaSearch
from scraper.parsers.manganelo import Manganelo, ManganeloMangaParser, ManganeloSearch
from scraper.parsers.manganato import Manganato, ManganatoMangaParser, ManganatoSearch
from scraper.parsers.mangapark import Mangapark, MangaparkMangaParser, MangaparkSearch
from scraper.parsers.mangago import Mangago, MangagoMangaParser, MangagoSearch
from scraper.parsers.mangabuddy import (
    Mangabuddy,
    MangabuddyMangaParser,
    MangabuddySearch,
)
from scraper.parsers.mangareader import (
    MangaReader,
    MangaReaderMangaParser,
    MangaReaderSearch,
)
from scraper.parsers.mangafire import (
    Mangafire,
    MangafireMangaParser,
    MangafireSearch,
)

MangaParser = Union[
    MangaReaderMangaParser,
    MangaKakaMangaParser,
    ManganeloMangaParser,
    MangaFastMangaParser,
    ManganatoMangaParser,
    MangaparkMangaParser,
    MangagoMangaParser,
    MangabuddyMangaParser,
    MangafireMangaParser,
]
SearchParser = Union[
    MangaReaderSearch,
    MangaKakaSearch,
    ManganeloSearch,
    MangaFastSearch,
    ManganatoSearch,
    MangaparkSearch,
    MangagoSearch,
    MangabuddySearch,
]
SiteParser = Union[
    MangaReader,
    MangaKaka,
    Manganelo,
    MangaFast,
    Manganato,
    Mangapark,
    Mangago,
    Mangabuddy,
]


MangaParserClass = Union[
    Type[MangaReaderMangaParser],
    Type[MangaKakaMangaParser],
    Type[ManganeloMangaParser],
    Type[MangaFastMangaParser],
    Type[ManganatoMangaParser],
    Type[MangaparkMangaParser],
    Type[MangagoMangaParser],
    Type[MangabuddyMangaParser],
]
SearchParserClass = Union[
    Type[MangaReaderSearch],
    Type[MangaKakaSearch],
    Type[ManganeloSearch],
    Type[MangaFastSearch],
    Type[ManganatoSearch],
    Type[MangaparkSearch],
    Type[MangagoSearch],
    Type[MangabuddySearch],
]
SiteParserClass = Union[
    Type[MangaReader],
    Type[MangaKaka],
    Type[Manganelo],
    Type[MangaFast],
    Type[Manganato],
    Type[Mangapark],
    Type[Mangago],
    Type[Mangabuddy],
]
