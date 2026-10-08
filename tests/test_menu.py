import pytest

from scraper.exceptions import InvalidOption, NoSearchResultsFound
from scraper.menu import SearchMenu
from tests.helpers import METADATA, TABLE, MockedSearch


def test_generate_search_menu_table():
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    table = search_menu.table()
    assert table == TABLE


def test_searchmenu_attributes():
    search_menu = SearchMenu("dragon-ball", MockedSearch)

    # MockedSearch returns METADATA, so the menu options should equal it
    assert search_menu.choices == TABLE
    assert search_menu.options == METADATA


@pytest.mark.parametrize(
    "selected,expected",
    [("1", METADATA["1"]), ("6", METADATA["6"])],
)
def test_handle_options_returns_selected_result(selected, expected, monkeypatch):
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    monkeypatch.setattr("builtins.input", lambda x: selected)
    assert search_menu.handle_options() == expected


def test_no_results_ends_the_search_instead_of_prompting(monkeypatch):
    # MangaFire's search returns {} when it times out or finds nothing (other
    # sites raise NoSearchResultsFound themselves). The menu used to print an
    # empty table and wait at the ">>" prompt for a choice that can't exist,
    # which looked like a hang. It must raise, so cli_entry reports it and exits.
    class EmptySearch:
        def search(self, query):
            return {}

    def no_prompt(_):
        raise AssertionError("must not prompt when there is nothing to choose")

    monkeypatch.setattr("builtins.input", no_prompt)
    with pytest.raises(NoSearchResultsFound, match="naruto"):
        SearchMenu(["naruto"], EmptySearch).handle_options()


def test_handle_options_invalid_choice_raises(monkeypatch):
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
                    latest_chapter="108",
                    source="mangafire",
                ),
                "2": SearchResult(
                    title="Pokémon Adventures",  # accented
                    manga_url="pokemon",
                    latest_chapter="600",
                    source="mangafire",
                ),
            }

    menu = SearchMenu("x", UnicodeSearch)
    table = menu.table()
    assert "鋼の錬金術師" in table
    assert "Pokémon Adventures" in table
