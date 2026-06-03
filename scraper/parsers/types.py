"""
Parser type aliases.

Since every site/manga/search parser derives from the ``Base*`` classes, these
aliases are just the base types -- no need to enumerate every concrete parser in
a ``Union`` (which also forced this module to import all of them). Sources are
discovered via ``scraper.registry`` now, not these unions (refactoring-plan
§3.3 / §5.3).
"""

from typing import Optional, Protocol, Type

from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser

MangaParser = BaseMangaParser
SearchParser = BaseSearchParser
SiteParser = BaseSiteParser

MangaParserClass = Type[BaseMangaParser]
SearchParserClass = Type[BaseSearchParser]


class SiteParserClass(Protocol):
    """
    The constructor contract every registered site parser exposes: callable with
    an optional ``manga_url``. Concrete parsers (``Mangafire(manga_url=None)``,
    etc.) satisfy this even though the abstract ``BaseSiteParser.__init__`` takes
    more args -- so code that does ``parser(manga_url)`` / ``parser()`` type-checks
    against this instead of the base's internal signature.
    """

    def __call__(self, manga_url: Optional[str] = None) -> BaseSiteParser: ...
