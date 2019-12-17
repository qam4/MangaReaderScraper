from typing import Any, Dict, List, Optional

from scraper.exceptions import InvalidOption
from scraper.parsers import MangaReaderSearch
from scraper.tables import TableProducer


class Menu:
    """
    Base class for all menus.
    """

    def __init__(
        self,
        options: Dict[str, str],
        choices: Optional[str] = None,
        parent: Optional["Menu"] = None,
    ) -> None:
        self.parent: Menu = parent
        self.options: Dict[str, str] = self._add_parent_to_options(options)
        self.choices: str = self._add_back_to_choices(choices)

    def handle_options(self) -> Any:
        """
        Extract and execute a method from self.options
        """
        try:
            print(self.choices)
            choice = input(">> ")
            item = self.options[choice]
            return item
        except KeyError:
            raise InvalidOption(
                f"{choice} is invalid. Choose an option from "
                f"{', '.join(self.options.keys())}"
            )

    def _add_parent_to_options(self, options: Dict[str, Any]) -> Dict[str, str]:
        """
        Modify options to include parent menu
        """
        if self.parent:
            number_options = len(options)
            new_option_key = str(number_options + 1)
            options[new_option_key] = self.parent
        return options

    def _add_back_to_choices(self, choices: str) -> str:
        """
        Modify choices to include back option to parent menu
        """
        if not self.parent:
            return choices
        num = len(self.options)
        if choices:
            return f"{choices}\n{num}. Back"
        else:
            return f"{num}. Back"

    @classmethod
    def from_list(cls, l: List[str]) -> "Menu":
        """
        Constructs self._options and self.choices from a list.
        """
        options = {str(k + 1): i for k, i in enumerate(l)}
        choices = "\n".join("{}. {}".format(k, i) for k, i in sorted(options.items()))
        return cls(options, choices)


class SearchMenu(Menu):
    def __init__(self, query: List[str]) -> None:
        self.search_results: TableProducer = self._search(query)
        choices: str = self.search_results.table
        options: Dict[str, str] = self._create_options()
        Menu.__init__(self, options, choices)

    def _search(self, query: List[str]) -> TableProducer:
        """
        Search for query and return Search object
        """
        mangasearch = MangaReaderSearch(query)
        factory = TableProducer()
        factory.generate(mangasearch)
        return factory

    def _create_options(self) -> Dict[str, str]:
        """
        Take number and url from search object
        """
        return {k: v["manga_url"] for k, v in self.search_results.results.items()}
