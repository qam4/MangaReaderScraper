"""
Scraper & parser for https://mangafire.to

How MangaFire works (and why this parser is shaped the way it is):

  * The site is behind Cloudflare, so plain requests fail. We drive a real
    browser (nodriver) for any HTML fetch.
  * A chapter's page images are NOT in the HTML. The reader fires a single
    JSON call ``/ajax/read/chapter/<id>?vrf=<token>`` that returns every
    page's image URL plus a per-page scramble ``offset``. The ``vrf`` token is
    computed by the site's obfuscated JS, so we let the browser fire the call
    and re-fetch that URL from inside the page (Cloudflare-cleared context).
  * Some images are deliberately scrambled (offset > 0): the image is sliced
    into a grid and the slices shifted. We reverse it (descramble) on download.
    Algorithm ported from the keiyoushi Tachiyomi extension's ImageInterceptor.

Image downloads use curl_cffi (Chrome TLS impersonation) with the browser's
cookies + a Referer header, which the CDN requires.
"""

import asyncio
import io
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup
from PIL import Image

from scraper.exceptions import MangaDoesNotExist, VolumeDoesntExist
from scraper.new_types import SearchResults
from scraper.parsers.base import BaseMangaParser, BaseSearchParser, BaseSiteParser

logger = logging.getLogger(__name__)

# --- descramble constants (from MangaFire's all.js, mirrored by the extension) ---
PIECE_SIZE = 200
MIN_SPLIT_COUNT = 5

# the page-list ajax calls we want to intercept
_AJAX_MARKERS = ("ajax/read/chapter", "ajax/read/volume")
# fragment we tack onto scrambled image urls so page_data knows to descramble
_SCRAMBLE_TAG = "scrambled"


# ============================== descramble ===============================

def _ceil_div(a: int, b: int) -> int:
    return (a + (b - 1)) // b


def descramble(data: bytes, offset: int) -> bytes:
    """Reverse MangaFire's slice-shuffle. Returns JPEG bytes."""
    src = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = src.size
    out = Image.new("RGB", (w, h))

    piece_w = min(PIECE_SIZE, _ceil_div(w, MIN_SPLIT_COUNT))
    piece_h = min(PIECE_SIZE, _ceil_div(h, MIN_SPLIT_COUNT))
    x_max = _ceil_div(w, piece_w) - 1
    y_max = _ceil_div(h, piece_h) - 1

    for y in range(y_max + 1):
        for x in range(x_max + 1):
            x_dst = piece_w * x
            y_dst = piece_h * y
            pw = min(piece_w, w - x_dst)
            ph = min(piece_h, h - y_dst)

            x_src = piece_w * (x if x == x_max else (x_max - x + offset) % x_max)
            y_src = piece_h * (y if y == y_max else (y_max - y + offset) % y_max)

            piece = src.crop((x_src, y_src, x_src + pw, y_src + ph))
            out.paste(piece, (x_dst, y_dst))

    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90)
    return buf.getvalue()


# ========================= browser page-list capture =====================

async def _capture_page_list(chapter_url: str, timeout: float = 45.0):
    """Open a chapter in nodriver, grab the page-list JSON & cookies.

    Returns (images, cookies):
      images  -> list of [url, ?, offset] entries
      cookies -> dict for the CDN image downloads

    A CDP event handler only RECORDS the ajax url (calling tab.send inside a
    handler deadlocks nodriver's receive loop). We then re-fetch that url from
    inside the page via fetch(), which avoids the flaky getResponseBody call.
    """
    import nodriver as nd
    from nodriver import cdp

    profile = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
    browser = await nd.start(
        user_data_dir=profile, headless=False, sandbox=False, no_sandbox=True
    )
    try:
        tab = await browser.get("about:blank")
        state: Dict[str, Optional[str]] = {"url": None}
        found = asyncio.Event()

        def matches(url: str) -> bool:
            return any(m in url for m in _AJAX_MARKERS)

        async def on_request(evt: cdp.network.RequestWillBeSent):
            if state["url"] is None and matches(evt.request.url):
                state["url"] = evt.request.url
                found.set()

        async def on_response(evt: cdp.network.ResponseReceived):
            if state["url"] is None and matches(evt.response.url):
                state["url"] = evt.response.url
                found.set()

        tab.add_handler(cdp.network.RequestWillBeSent, on_request)
        tab.add_handler(cdp.network.ResponseReceived, on_response)
        await tab.send(cdp.network.enable())

        await tab.get(chapter_url)
        await asyncio.wait_for(found.wait(), timeout=timeout)
        page_list_url = state["url"]

        js = (
            "(async () => {"
            f"  const r = await fetch({json.dumps(page_list_url)}, "
            "    {credentials: 'include', "
            "     headers: {'X-Requested-With': 'XMLHttpRequest'}});"
            "  return await r.text();"
            "})()"
        )
        body = await asyncio.wait_for(tab.evaluate(js, await_promise=True), timeout=30)
        data = json.loads(body)
        images = data["result"]["images"]

        cookies: Dict[str, str] = {}
        try:
            raw = await asyncio.wait_for(tab.send(cdp.network.get_cookies()), timeout=10)
            cookies = {c.name: c.value for c in raw}
        except Exception as err:  # pragma: no cover - best effort
            logger.warning(f"could not read cookies via CDP: {err}")

        return images, cookies
    finally:
        browser.stop()


def _encode_page_url(url: str, offset: int) -> str:
    """Carry the scramble offset in the url fragment, like the Tachiyomi ext."""
    if offset and offset > 0:
        return f"{url}#{_SCRAMBLE_TAG}_{offset}"
    return url


async def _fetch_in_browser(establish_url: str, ajax_url: str,
                            timeout: float = 45.0) -> str:
    """Navigate to ``establish_url`` (for Cloudflare clearance + cookies), then
    fetch ``ajax_url`` from inside the page and return the response text.

    Used for endpoints whose URL we know up front (no vrf token), e.g. the
    chapter list ``/ajax/manga/<id>/chapter/en``.
    """
    import nodriver as nd

    profile = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
    browser = await nd.start(
        user_data_dir=profile, headless=False, sandbox=False, no_sandbox=True
    )
    try:
        page = await browser.get(establish_url)
        await page.wait(5)
        js = (
            "(async () => {"
            f"  const r = await fetch({json.dumps(ajax_url)}, "
            "    {credentials: 'include', "
            "     headers: {'X-Requested-With': 'XMLHttpRequest'}});"
            "  return await r.text();"
            "})()"
        )
        text = await asyncio.wait_for(page.evaluate(js, await_promise=True),
                                      timeout=timeout)
        return text
    finally:
        browser.stop()


# ================================ parser =================================

class MangafireMangaParser(BaseMangaParser):
    """
    Scrapes & parses a specific manga page on https://mangafire.to
    """

    def __init__(self, manga_url: str, base_url: str = "https://mangafire.to") -> None:
        super().__init__(manga_url, base_url)
        self.headers = {"Referer": f"{self.base_url}/"}
        # cookies harvested from the browser during page_urls, reused in page_data
        self.cookies: Dict[str, str] = {}

    def volume_url(self, volume: str) -> str:
        return f"{self.base_url}/read/{self.manga_url}/en/chapter-{volume}"

    def page_urls(self, volume: str) -> List[Tuple[int, str]]:
        """
        Return [(page_number, url)] for every page in a chapter.

        Scrambled pages carry a ``#scrambled_<offset>`` fragment so page_data
        knows to descramble them.
        """
        chapter_url = self.volume_url(volume)
        logger.info(f"Fetching page list for {chapter_url}")
        try:
            images, cookies = asyncio.run(_capture_page_list(chapter_url))
        except asyncio.TimeoutError:
            raise VolumeDoesntExist(
                f"Timed out getting page list for {self.manga_url} chapter {volume} "
                "(Cloudflare challenge or chapter does not exist)"
            )
        self.cookies = cookies

        page_urls: List[Tuple[int, str]] = []
        for page_num, entry in enumerate(images, start=1):
            url = entry[0]
            offset = int(entry[2]) if len(entry) > 2 and entry[2] else 0
            page_urls.append((page_num, _encode_page_url(url, offset)))
        return page_urls

    def page_data(self, page_url: Tuple[int, str]) -> Tuple[int, bytes, str]:
        """
        Download a page image (curl_cffi + Chrome impersonation) and descramble
        it if the url fragment marks it scrambled. Overrides the base requests
        implementation because the CDN rejects non-browser TLS fingerprints.
        """
        from curl_cffi import requests as creq

        page_num, raw_url = page_url
        url, _, frag = raw_url.partition("#")
        offset = 0
        if frag.startswith(f"{_SCRAMBLE_TAG}_"):
            offset = int(frag.rsplit("_", 1)[-1])

        attempt, max_tries = 0, 5
        content = b""
        while attempt < max_tries:
            try:
                session = creq.Session(impersonate="chrome")
                if self.cookies:
                    session.cookies.update(self.cookies)
                resp = session.get(url, headers=self.headers, timeout=60)
                if resp.status_code == 200:
                    content = resp.content
                    break
                logger.warning(
                    f"page {page_num} attempt {attempt + 1}/{max_tries} "
                    f"status {resp.status_code}: {url}"
                )
            except Exception as err:
                logger.warning(
                    f"page {page_num} attempt {attempt + 1}/{max_tries} failed: {err}"
                )
            attempt += 1

        if not content:
            logger.error(f"Download FAILED page {page_num} at {url}")
            return (int(page_num), self.create_page(f"Page {page_num} missing\n{url}"),
                    "missing")

        try:
            if offset > 0:
                content = descramble(content, offset)
            else:
                # validate it is a real image
                img = Image.open(io.BytesIO(content))
                img.verify()
        except Exception as err:
            logger.error(f"page {page_num} at {url} corrupted: {err}")
            return (int(page_num),
                    self.create_page(f"Page {page_num} corrupted.\n{err}\n{url}"),
                    "corrupted")

        return (int(page_num), content, "success")

    def all_volume_ids(self) -> Iterable[str]:
        """
        Get the list of all chapter numbers for a manga.

        Uses MangaFire's authoritative chapter-list endpoint
        ``/ajax/manga/<id>/chapter/en`` (no vrf token needed), where ``<id>``
        is the part of the slug after the last ``.`` -- e.g.
        ``ad-astra-scipio-and-hanniball.lww3`` -> ``lww3``. The endpoint
        returns JSON ``{"result": "<li> markup>"}`` whose anchors carry
        ``data-number`` attributes. This mirrors the keiyoushi Tachiyomi
        extension (chapterListRequest).
        """
        manga_id = self.manga_url.rsplit(".", 1)[-1]
        manga_page = f"{self.base_url}/manga/{self.manga_url}"
        ajax_url = f"{self.base_url}/ajax/manga/{manga_id}/chapter/en"
        logger.info(f"Chapter list url={ajax_url}")

        try:
            body = asyncio.run(_fetch_in_browser(manga_page, ajax_url))
        except asyncio.TimeoutError:
            raise MangaDoesNotExist(
                f"Timed out fetching chapter list for {self.manga_url}"
            )

        try:
            result_html = json.loads(body)["result"]
        except (ValueError, KeyError) as err:
            raise MangaDoesNotExist(
                f"Unexpected chapter-list response for {self.manga_url}: {err}"
            )

        fragment = BeautifulSoup(result_html, "lxml")
        volume_ids = set()
        for tag in fragment.find_all(attrs={"data-number": True}):
            volume_ids.add(tag["data-number"])
        # fallback: pull chapter numbers out of hrefs
        if not volume_ids:
            for a in fragment.find_all("a", href=re.compile(r"chapter-[\d.]+")):
                m = re.search(r"chapter-([\d.]+)", a.get("href", ""))
                if m:
                    volume_ids.add(m.group(1))

        if not volume_ids:
            raise MangaDoesNotExist(
                f"No chapters found for {self.manga_url} (bad slug or page blocked)"
            )
        return sorted(volume_ids, key=lambda v: float(v))


class MangafireSearch(BaseSearchParser):
    """
    Parses search queries from mangafire.to.

    MangaFire's keyword search is gated behind a ``vrf`` token computed by the
    site's obfuscated JS, so we can't hit a plain search URL. Instead we mirror
    the keiyoushi Tachiyomi extension: load the site in a browser, type the
    query into the live search box, and intercept the ``ajax/manga/search``
    response the page fires itself. That response is an HTML fragment of result
    cards which we parse for ``/manga/<slug>`` links.
    """

    def __init__(self, query: str, base_url: str = "https://mangafire.to") -> None:
        super().__init__(query, base_url)

    def _parse_results(self, html_fragment: str, start: int) -> SearchResults:
        """
        Parse the search result cards. Real shape (from ajax/manga/search):

            <div class="original card-sm body">
              <a class="unit" href="/manga/<slug>">
                <div class="poster">...</div>
                <div class="info">
                  <h6>Title</h6>
                  <div><span>Status</span><span>Chap 81</span><span>Vol 13</span></div>
                </div>
              </a>
              ...
            </div>
            <div><a class="btn ..." href="/filter?...">View all Results</a></div>

        We only want the ``a.unit`` cards (the trailing "View all" link is not
        a .unit, so it is naturally excluded).
        """
        soup = BeautifulSoup(html_fragment, "lxml")
        metadata: SearchResults = {}
        key = start
        seen = set()
        for unit in soup.select("a.unit"):
            m = re.search(r"/manga/([^/?#]+)", unit.get("href", ""))
            if not m:
                continue
            slug = m.group(1)
            if slug in seen:
                continue
            seen.add(slug)

            title_tag = unit.find("h6")
            title = title_tag.get_text(strip=True) if title_tag else slug

            # latest chapter: the span that looks like "Chap 81"
            chapters = ""
            for span in unit.select(".info span"):
                txt = span.get_text(strip=True)
                cm = re.search(r"Chap(?:ter)?\s*([\d.]+)", txt, re.I)
                if cm:
                    chapters = cm.group(1)
                    break

            metadata[str(key)] = {
                "title": title,
                "manga_url": slug,
                "chapters": chapters,
                "source": "mangafire",
            }
            key += 1
        return metadata

    async def _search_in_browser(self, timeout: float = 45.0) -> str:
        import nodriver as nd
        from nodriver import cdp

        profile = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
        browser = await nd.start(
            user_data_dir=profile, headless=False, sandbox=False, no_sandbox=True
        )
        try:
            tab = await browser.get(f"{self.base_url}/home")

            state: Dict[str, Optional[str]] = {"url": None}
            found = asyncio.Event()

            async def on_response(evt: cdp.network.ResponseReceived):
                if state["url"] is None and "ajax/manga/search" in evt.response.url:
                    state["url"] = evt.response.url
                    found.set()

            tab.add_handler(cdp.network.ResponseReceived, on_response)
            await tab.send(cdp.network.enable())
            await tab.wait(3)

            # type the query into the live search box to trigger the vrf'd call
            js_type = (
                "(() => {"
                "  const i = document.querySelector("
                "    '.search-inner input[name=keyword], input[name=keyword]');"
                "  if (!i) return false;"
                f"  i.value = {json.dumps(self.query)};"
                "  i.dispatchEvent(new Event('input', {bubbles:true}));"
                "  i.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));"
                "  return true;"
                "})()"
            )
            await tab.evaluate(js_type)

            await asyncio.wait_for(found.wait(), timeout=timeout)
            search_url = state["url"]
            js_fetch = (
                "(async () => {"
                f"  const r = await fetch({json.dumps(search_url)}, "
                "    {credentials: 'include', "
                "     headers: {'X-Requested-With': 'XMLHttpRequest'}});"
                "  return await r.text();"
                "})()"
            )
            body = await asyncio.wait_for(
                tab.evaluate(js_fetch, await_promise=True), timeout=30
            )
            # response is JSON {"result": {"html": "..."}} or {"result": "..."}
            try:
                result = json.loads(body)["result"]
                if isinstance(result, dict):
                    return result.get("html", "")
                return result
            except (ValueError, KeyError):
                return body
        finally:
            browser.stop()

    def search(self, start: int = 1) -> SearchResults:
        logger.info(f"Searching mangafire for: {self.query}")
        try:
            html_fragment = asyncio.run(self._search_in_browser())
        except asyncio.TimeoutError:
            logger.error(
                "MangaFire search timed out (could not capture the vrf'd "
                "ajax/manga/search call). Use a direct manga slug with --manga."
            )
            return {}
        return self._parse_results(html_fragment, start)


class Mangafire(BaseSiteParser):
    """
    Scraper & parser for mangafire.to
    """

    def __init__(self, manga_url: Optional[str] = None) -> None:
        super().__init__(
            manga_url=manga_url,
            base_url="https://mangafire.to",
            manga_parser=MangafireMangaParser,
            search_parser=MangafireSearch,
        )
