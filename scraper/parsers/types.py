"""
Parser type aliases.

Since every site/manga/search parser derives from the ``Base*`` classes, these
aliases are just the base types -- no need to enumerate every concrete parser in
a ``Union`` (which also forced this module to import all of them). Sources are
discovered via ``scraper.registry`` now, not these unions (refactoring-plan
§3.3 / §5.3).
"""

from typing import Type

from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser

MangaParser = BaseMangaParser
SearchParser = BaseSearchParser
SiteParser = BaseSiteParser

MangaParserClass = Type[BaseMangaParser]
SearchParserClass = Type[BaseSearchParser]
SiteParserClass = Type[BaseSiteParser]
