"""
Custom exceptions
"""


class VolumeDoesntExist(Exception):
    pass


class MangaDoesNotExist(Exception):
    pass


class PageDoesNotExist(Exception):
    pass


class PageAlreadyPresent(Exception):
    pass


class VolumeAlreadyPresent(Exception):
    pass


class VolumeAlreadyExists(Exception):
    pass


class InvalidOption(Exception):
    pass


class MangaParserNotSet(Exception):
    pass


class CannotExtractChapter(Exception):
    pass


class NoSearchResultsFound(Exception):
    """Raised by the search parser layer when a query yields no results.

    The parser layer must not terminate the process; the CLI entry point maps
    this to a clean exit.
    """

    pass
