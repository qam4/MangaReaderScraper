"""
Scraper & parser for https://mangafire.to, as the site was rebuilt in 2026.

How the rebuilt MangaFire works (from the October 2026 probe captures, see
tests/test_files/mangafire/):

  * All data comes from a JSON API under ``/api/``:
      search    ``/api/titles?keyword=<q>&...&page=1&limit=30``
      details   ``/api/titles/<hid>``  (authors are here)
      chapters  ``/api/titles/<hid>/chapters?language=en&...&page=N&limit=20``
      pages     ``/api/chapters/<chapter id>``  (the page image urls)
  * Every call carries a ``vrf`` token that the site's own script computes
    from the full address, and the API answers 403 without it. So the browser
    makes the calls: we open the site's pages, let its code request the data,
    catch those requests and re-fetch them in-page
    (``BrowserFetcher.capture_xhr`` / ``capture_xhr_pages``). We never build a
    token ourselves.
  * A series is addressed as ``<hid>-<slug>`` (``92kk8-naruto``), the path
    after ``/title/``. The rest of the app identifies chapters by their number;
    the API's chapter id is only needed for the reader url
    ``/title/<hid>-<slug>/chapter/<id>``.
  * The chapter list is paged 20 at a time with buttons, not links, so it is
    walked by pressing the page's own "Next page" button.
  * Page images download straight from the image servers (plain requests
    worked in the probe). The page list carries only url/width/height: the old
    site's image scrambling is gone.
"""

import io
import json
import logging
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from PIL import Image

from scraper.exceptions import ChapterDoesntExist, MangaDoesNotExist
from scraper.fetchers import BrowserFetcher, CaptureTriggerFailed, download_image
from scraper.new_types import SearchResult, SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser
from scraper.registry import register_source
from scraper.selection import sort_chapter_ids

logger = logging.getLogger(__name__)

BASE_URL = "https://mangafire.to"

# The header search box on the home page:
#   <input class="input input--icon input--hint" placeholder="Search titles…">
_SEARCH_BOX = 'input[placeholder^="Search"]'

# Press the chapter list's own "Next page" button (the list is paged with
# buttons, not links). False when there is no such button to press, which
# capture_xhr_pages turns into a clear error instead of a partial list.
_NEXT_PAGE_JS = (
    "(() => {"
    "  const b = document.querySelector("
    "    '.title-detail__chapters-pager button[aria-label=\"Next page\"]');"
    "  if (!b || b.disabled) return false;"
    "  b.click();"
    "  return true;"
    "})()"
)


# ============================ pure helpers ===============================


def _series_path(manga_url: str) -> str:
    """``92kk8-naruto`` from the slug itself or a pasted series/reader url."""
    text = manga_url.strip()
    match = re.search(r"/title/([^/?#]+)", text)
    return match.group(1) if match else text.strip("/")


def _path_is(path: str) -> Callable[[str], bool]:
    """Predicate: a request url whose path is exactly ``path``, any query."""
    return lambda url: urlparse(url).path == path


def _is_search_results_request(url: str) -> bool:
    """The submitted search (``keyword=`` with ``page=``). Not the box's
    as-you-type suggestions (no ``page=``), nor the home page's own lists (no
    ``keyword=``)."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return parsed.path == "/api/titles" and "keyword" in query and "page" in query


def _chapter_number(value) -> Optional[str]:
    """The app's chapter id for an API ``number``: 700 -> "700",
    700.0 -> "700", 700.5 -> "700.5"; strings are kept as they are."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    text = str(value).strip()
    return text or None


def _parse_search_payload(payload: dict, start: int) -> SearchResults:
    """Search results from ``/api/titles?keyword=...``."""
    results: SearchResults = {}
    key = start
    for item in payload.get("items") or []:
        path = _series_path(item["url"]) if item.get("url") else ""
        if not path and item.get("hid") and item.get("slug"):
            path = f"{item['hid']}-{item['slug']}"
        if not path:
            continue
        results[str(key)] = SearchResult(
            title=item.get("title") or path,
            manga_url=path,
            latest_chapter=_chapter_number(item.get("latestChapter")) or "",
            source="mangafire",
        )
        key += 1
    return results


def _has_next_page(body: str) -> bool:
    """Whether a chapter-list page says another page follows."""
    try:
        return bool((json.loads(body).get("meta") or {}).get("hasNext"))
    except (ValueError, AttributeError):
        return False


def _chapter_map_from_pages(bodies: Iterable[str]) -> Dict[str, str]:
    """{chapter number: API chapter id} across every chapter-list page.

    A number listed more than once (several uploads of one chapter) keeps the
    first id seen; the page lists newest first.
    """
    chapters: Dict[str, str] = {}
    for body in bodies:
        for item in json.loads(body).get("items") or []:
            number = _chapter_number(item.get("number"))
            chapter_id = item.get("id")
            if number is None or chapter_id is None:
                continue
            if number in chapters:
                logger.debug(
                    f"chapter {number} is listed more than once; keeping id "
                    f"{chapters[number]}, skipping {chapter_id}"
                )
                continue
            chapters[number] = str(chapter_id)
    return chapters


def _page_urls_from_payload(payload: dict) -> List[Tuple[int, str]]:
    """[(page number, image url)] from ``/api/chapters/<id>``."""
    pages = (payload.get("data") or {}).get("pages") or []
    urls = [page["url"] for page in pages if page.get("url")]
    return list(enumerate(urls, start=1))


def _authors_from_title_payload(payload: dict) -> Optional[str]:
    """Comma-joined, de-duplicated author names from ``/api/titles/<hid>``."""
    names: List[str] = []
    for author in (payload.get("data") or {}).get("authors") or []:
        name = (author.get("title") or "").strip()
        if name and name not in names:
            names.append(name)
    return ", ".join(names) if names else None


# ================================ parser =================================


class MangafireMangaParser(BaseMangaParser):
    """
    A series on https://mangafire.to, addressed as ``<hid>-<slug>``
    """

    def __init__(self, manga_url: str, base_url: str = BASE_URL) -> None:
        super().__init__(_series_path(manga_url), base_url)
        self.headers = {"Referer": f"{self.base_url}/"}
        # cookies from the reader page's browser session, reused for the images
        self.cookies: Dict[str, str] = {}
        self._chapters: Optional[Dict[str, str]] = None

    @property
    def hid(self) -> str:
        return self.manga_url.split("-", 1)[0]

    @property
    def series_url(self) -> str:
        return f"{self.base_url}/title/{self.manga_url}"

    def _chapter_ids(self) -> Dict[str, str]:
        """{chapter number: API chapter id}, loaded once: open the series page
        and walk its chapter list with the "Next page" button."""
        if self._chapters is None:
            try:
                bodies = BrowserFetcher().capture_xhr_pages(
                    self.series_url,
                    _path_is(f"/api/titles/{self.hid}/chapters"),
                    _NEXT_PAGE_JS,
                    _has_next_page,
                )
                chapters = _chapter_map_from_pages(bodies)
            except TimeoutError:
                raise MangaDoesNotExist(
                    f"Timed out loading the chapter list of {self.manga_url} "
                    f"({self.series_url})"
                )
            except CaptureTriggerFailed as err:
                # a partial list would shift every later volume: refuse it
                raise MangaDoesNotExist(
                    f"Could not page through the chapter list of "
                    f"{self.manga_url}: {err}"
                )
            except ValueError as err:
                raise MangaDoesNotExist(
                    f"Unreadable chapter list for {self.manga_url}: {err}"
                )
            if not chapters:
                raise MangaDoesNotExist(f"No chapters found for {self.manga_url}")
            self._chapters = chapters
        return self._chapters

    def all_chapter_ids(self) -> Iterable[str]:
        return sort_chapter_ids(self._chapter_ids().keys())

    def chapter_url(self, chapter: str) -> str:
        chapter_id = self._chapter_ids().get(chapter)
        if chapter_id is None:
            raise ChapterDoesntExist(f"{self.manga_url} has no chapter {chapter}")
        return f"{self.series_url}/chapter/{chapter_id}"

    def page_urls(self, chapter: str) -> List[Tuple[int, str]]:
        """Open the chapter's reader page and catch its own page-list request."""
        url = self.chapter_url(chapter)
        chapter_id = self._chapter_ids()[chapter]
        logger.info(f"Fetching page list for {url}")
        try:
            body, cookies = BrowserFetcher().capture_xhr(
                url, _path_is(f"/api/chapters/{chapter_id}"), with_cookies=True
            )
            pages = _page_urls_from_payload(json.loads(body))
        except TimeoutError:
            raise ChapterDoesntExist(
                f"Timed out getting the page list of {self.manga_url} chapter "
                f"{chapter} ({url})"
            )
        except ValueError as err:
            raise ChapterDoesntExist(
                f"Unreadable page list for {self.manga_url} chapter {chapter}: {err}"
            )
        self.cookies = cookies
        return pages

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """Download a page image and check it is one."""
        page_num, url = page_url
        content = download_image(
            url,
            headers=self.headers,
            cookies=self.cookies or None,
            label=f"page {page_num}",
        )
        if content is None:
            return (
                int(page_num),
                self.create_page(f"Page {page_num} missing\n{url}"),
                "missing",
            )
        try:
            Image.open(io.BytesIO(content)).verify()
        except Exception as err:
            logger.error(f"page {page_num} at {url} corrupted: {err}")
            return (
                int(page_num),
                self.create_page(f"Page {page_num} corrupted.\n{err}\n{url}"),
                "corrupted",
            )
        return (int(page_num), content, "success")

    def author(self) -> Optional[str]:
        """Author(s) from the series details the series page requests. Best
        effort: any failure returns None (the builder treats author as
        optional). This loads the series page once more than all_chapter_ids,
        as the old parser did."""
        try:
            body, _ = BrowserFetcher().capture_xhr(
                self.series_url, _path_is(f"/api/titles/{self.hid}")
            )
            return _authors_from_title_payload(json.loads(body))
        except Exception as err:
            logger.debug(f"author lookup failed for {self.series_url}: {err}")
            return None


class MangafireSearch(BaseSearchParser):
    """
    Search on mangafire.to: type the query into the home page's search box and
    press Enter, then catch the results request the page makes (its vrf token
    is computed by the site's script).
    """

    def __init__(self, query: str, base_url: str = BASE_URL) -> None:
        super().__init__(query, base_url)

    def search(self, start: int = 1) -> SearchResults:
        logger.info(f"Searching mangafire for: {self.query}")
        try:
            body, _ = BrowserFetcher().capture_xhr(
                f"{self.base_url}/home",
                _is_search_results_request,
                type_into=(_SEARCH_BOX, self.query),
            )
            payload = json.loads(body)
        except CaptureTriggerFailed as err:
            # The error says which page the browser was on; a Cloudflare check
            # there means the scraper's browser was held up, not that the site
            # changed.
            logger.error(f"MangaFire search: couldn't find the search box ({err}).")
            return {}
        except TimeoutError:
            logger.error(
                "MangaFire search timed out: the site sent no search request "
                "after the query was typed. Use a direct series id with --manga "
                "(e.g. 92kk8-naruto)."
            )
            return {}
        except ValueError as err:
            logger.error(f"MangaFire search: unreadable reply ({err}).")
            return {}
        return _parse_search_payload(payload, start)


@register_source("mangafire")
class Mangafire(BaseSiteParser):
    """
    Scraper & parser for mangafire.to
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url=BASE_URL,
            manga_parser=MangafireMangaParser,
            search_parser=MangafireSearch,
        )
