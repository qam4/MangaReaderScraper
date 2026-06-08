"""
Tests for the source registry.
"""

from scraper.parsers.base import BaseSiteParser
from scraper.registry import available_sources, get_source, register_source


def test_all_known_sources_registered():
    names = available_sources()
    for expected in [
        "mangakaka",
        "manganelo",
        "manganato",
        "mangago",
        "mangabuddy",
        "mangafire",
    ]:
        assert expected in names


def test_get_source_returns_site_parser_class():
    cls = get_source("mangafire")
    assert cls is not None
    assert issubclass(cls, BaseSiteParser)
    assert cls.__name__ == "Mangafire"


def test_get_source_unknown_returns_none():
    assert get_source("does-not-exist") is None


def test_available_sources_is_sorted():
    names = available_sources()
    assert names == sorted(names)


def test_register_source_decorator_adds_and_sets_name():
    @register_source("temp-test-source")
    class _Tmp(BaseSiteParser):
        def __init__(self, manga_url=None):
            super().__init__(
                manga_url=manga_url,
                base_url="x",
                manga_parser=object,  # type: ignore[arg-type]
                search_parser=object,  # type: ignore[arg-type]
            )

    assert get_source("temp-test-source") is _Tmp
    assert _Tmp.source_name == "temp-test-source"
