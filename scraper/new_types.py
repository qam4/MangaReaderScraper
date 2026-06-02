"""
Custom type hints & aliases
"""

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

PageData = Tuple[int, bytes, str]
VolumeData = Tuple[str, Optional[Iterable[PageData]]]


@dataclass
class SearchResult:
    """
    A single manga search result.

    Replaces the old free-form ``Dict[str, str]`` so parsers can't silently emit
    the wrong key/type (refactoring-plan §3.4). Supports mapping-style access
    (``result["title"]``) for backwards compatibility with the menu and CLI,
    which still read these by key.

    ``__post_init__`` normalizes ``chapters`` to a string so a parser that
    assigns an int (the old ``chapters = 0`` fallback in mangabuddy/manganato/
    mangapark) can't violate the type the menu reads back. This is the single
    place that owns that normalization.
    """

    title: str
    manga_url: str
    chapters: str
    source: str

    def __post_init__(self) -> None:
        if self.chapters is None:
            self.chapters = ""
        elif not isinstance(self.chapters, str):
            self.chapters = str(self.chapters)

    def __getitem__(self, key: str) -> str:
        # backwards-compat shim for code that still treats results as dicts
        return getattr(self, key)

    def __setitem__(self, key: str, value: str) -> None:
        setattr(self, key, value)


# A page of search results, keyed by the 1-based selection number the menu shows.
SearchResults = Dict[str, SearchResult]
