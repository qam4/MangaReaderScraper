import pytest

from scraper.exceptions import InvalidOption
from scraper.menu import SearchMenu
from tests.helpers import METADATA, TABLE, MockedSearch


def test_generate_search_menu_table(mangareader_search_html):
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    table = search_menu.table()
    assert table == TABLE


def test_searchmenu_attributes(mangareader_search_html):
    search_menu = SearchMenu("dragon-ball", MockedSearch)

    # MockedSearch returns METADATA, so the menu options should equal it
    assert search_menu.choices == TABLE
    assert search_menu.options == METADATA


@pytest.mark.parametrize(
    "selected,expected",
    [("1", METADATA["1"]), ("6", METADATA["6"])],
)
def test_handle_options_returns_selected_result(
    selected, expected, monkeypatch, mangareader_search_html
):
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    monkeypatch.setattr("builtins.input", lambda x: selected)
    assert search_menu.handle_options() == expected


def test_handle_options_invalid_choice_raises(monkeypatch, mangareader_search_html):
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    monkeypatch.setattr("builtins.input", lambda x: "999")
    with pytest.raises(InvalidOption):
        search_menu.handle_options()


def test_table_preserves_unicode_titles():
    # D2: the old `.encode("ascii", errors="ignore")` dropped non-ASCII chars,
    # mangling Japanese/accented titles. The table must now render them intact.
    from scraper.new_types import SearchResult

    class UnicodeSearch:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, *args):
            return {
                "1": SearchResult(
                    title="鋼の錬金術師",  # Fullmetal Alchemist (JP)
                    manga_url="fma",
                    chapters="108",
                    source="mangafire",
                ),
                "2": SearchResult(
                    title="Pokémon Adventures",  # accented
                    manga_url="pokemon",
                    chapters="600",
                    source="mangafire",
                ),
            }

    menu = SearchMenu("x", UnicodeSearch)
    table = menu.table()
    assert "鋼の錬金術師" in table
    assert "Pokémon Adventures" in table
