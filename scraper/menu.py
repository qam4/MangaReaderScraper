from typing import List

from tabulate import tabulate  # type: ignore

from scraper.exceptions import InvalidOption
from scraper.new_types import SearchResults
from scraper.parsers.types import SiteParser, SiteParserClass
from scraper.utils import menu_input

# Titles longer than this are truncated in the results table.
TITLE_MAX_WIDTH = 70


class SearchMenu:
    """
    Render search results as a numbered table, prompt for a choice, and return
    the chosen result.

    This is deliberately flat: a single "render -> pick row -> return" step.
    There is no parent/child menu tree -- search is the only menu the CLI has.
    """

    def __init__(self, query: List[str], parser: SiteParserClass) -> None:
        self.parser: SiteParser = parser()
        self.search_results: SearchResults = self._search(query)
        self.options: SearchResults = self.search_results
        self.choices: str = self.table()

    def _search(self, query: List[str]) -> SearchResults:
        """
        Search for query and return the search results.
        """
        return self.parser.search(" ".join(query))

    def table(self) -> str:
        columns = ["", "Title", "Latest Volume", "Source"]
        data: List[List[str]] = []
        for number, metadata in self.search_results.items():
            # Render the title as-is (unicode): many manga have Japanese or
            # accented titles, and the old `.encode("ascii", errors="ignore")`
            # silently dropped those characters (mangling e.g. JP titles to "").
            title = metadata["title"]
            chapters = metadata["latest_chapter"]
            source = metadata["source"]
            title = (
                title
                if len(title) < TITLE_MAX_WIDTH
                else f"{title[:TITLE_MAX_WIDTH]}..."
            )
            data.append([number, title, chapters, source])

        return tabulate(data, headers=columns, tablefmt="psql")

    def handle_options(self):
        """
        Print the table, prompt for a row index, and return the chosen result.
        """
        print(self.choices)
        msg = "Select the index of the manga of your choice"
        choice = menu_input(msg)
        try:
            return self.options[choice]
        except KeyError:
            raise InvalidOption(
                f"{choice} is invalid. Choose an option from "
                f"{', '.join(self.options.keys())}"
            )
