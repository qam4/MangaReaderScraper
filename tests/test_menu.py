import pytest

from scraper.exceptions import InvalidOption
from scraper.menu import SearchMenu
from tests.helpers import TABLE, MockedSearch


def test_generate_search_menu_table(mangareader_search_html):
    search_menu = SearchMenu("dragon-ball", MockedSearch)
    table = search_menu.table()
    assert table == TABLE


def test_searchmenu_attributes(mangareader_search_html):
    search_menu = SearchMenu("dragon-ball", MockedSearch)

    exepected_options = {
        "1": {
            "chapters": "3",
            "manga_url": "dragon-ball-episode-of-bardock",
            "source": "mangareader",
            "title": "Dragon Ball: Episode of Bardock",
        },
        "2": {
            "chapters": "35",
            "manga_url": "dragon-ball-sd",
            "source": "mangareader",
            "title": "Dragon Ball SD",
        },
        "3": {
            "chapters": "4",
            "manga_url": "dragonball-next-gen",
            "source": "mangareader",
            "title": "DragonBall Next Gen",
        },
        "4": {
            "chapters": "520",
            "manga_url": "dragon-ball",
            "source": "mangareader",
            "title": "Dragon Ball",
        },
        "5": {
            "chapters": "3",
            "manga_url": "dragon-ball-z-rebirth-of-f",
            "source": "mangareader",
            "title": "Dragon Ball Z - Rebirth of F",
        },
        "6": {
            "chapters": "62",
            "manga_url": "dragon-ball-super",
            "source": "mangareader",
            "title": "Dragon Ball Super",
        },
    }
    assert search_menu.choices == TABLE
    assert search_menu.options == exepected_options


@pytest.mark.parametrize(
    "selected,expected", [("1", "dragon-ball"), ("6", "dragon-ball-super")]
)
def test_menu_options(selected, expected, monkeypatch, menu):
    monkeypatch.setattr("builtins.input", lambda x: selected)
    requested = menu.handle_options()
    assert requested == expected


def test_invalid_choic(monkeypatch, menu):
    monkeypatch.setattr("builtins.input", lambda x: "999")
    with pytest.raises(InvalidOption):
        menu.handle_options()


def test_parent_menu(monkeypatch, menu):
    assert menu.options["7"] == menu.parent
    monkeypatch.setattr("builtins.input", lambda x: "7")
    requested = menu.handle_options()
    assert requested == menu.parent


# def test_init_from_list():
#     menu = Menu.from_list(["a", "b", "c"])
#     assert menu.options == {"1": "a", "2": "b", "3": "c"}
#     assert menu.choices == "1. a\n2. b\n3. c"


def test_back_button(menu_no_choices):
    back_button = menu_no_choices.options.get("3")
    assert back_button == menu_no_choices.parent
