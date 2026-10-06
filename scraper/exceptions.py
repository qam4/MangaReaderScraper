"""
Custom exceptions
"""


class ChapterDoesntExist(Exception):
    pass


class MangaDoesNotExist(Exception):
    pass


class PageDoesNotExist(Exception):
    pass


class PageAlreadyPresent(Exception):
    pass


class ChapterAlreadyPresent(Exception):
    pass


class ChapterAlreadyExists(Exception):
    pass


class InvalidOption(Exception):
    pass


class MangaParserNotSet(Exception):
    pass


class CannotExtractChapter(Exception):
    pass


class OfflineSeriesNotFound(Exception):
    """``--offline`` was asked to bundle a series with no local record of it
    (no ``.series.json`` beside its chapters)."""


class NoSearchResultsFound(Exception):
    """Raised by the search parser layer when a query yields no results.

    The parser layer must not terminate the process; the CLI entry point maps
    this to a clean exit.
    """

    pass
