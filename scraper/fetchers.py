"""
Fetcher abstraction: typed backends replacing the ``get_html_from_url(url,
type="...")`` string switch (refactoring-plan §3.2 / §5.2).

A ``Fetcher`` knows how to GET a url and return a ``FetchResult``. Each backend
imports its heavy dependency **lazily inside the method**, never at module top,
so importing this module (and anything that declares a fetcher) costs nothing
until a fetch actually happens. This is what lets the test suite and the CLI
import without selenium/cloudscraper/nodriver installed.

Backends:
  * ``CurlCffiFetcher``      -- ``curl_cffi`` with Chrome TLS/JA3 impersonation
    (default for ``fetch_soup``; defeats fingerprint-based WAF blocking that
    plain ``requests`` trips, while keeping the ``requests`` API)
  * ``RequestsFetcher``      -- plain ``requests`` (lightweight fallback)
  * ``CloudscraperFetcher``  -- ``cloudscraper`` (Cloudflare IUAM bypass)
  * ``BrowserFetcher``       -- ``nodriver`` real browser; also exposes the two
    primitives the MangaFire work needed: ``fetch_json_in_page`` (navigate for
    Cloudflare clearance, then fetch a known ajax url from inside the page) and
    ``capture_xhr`` (intercept the first XHR matching a predicate, optionally
    after running a trigger script, then re-fetch it in-page).

A parser declares ``fetcher = BrowserFetcher`` instead of passing magic strings.

Note: this module is additive. Parsers are migrated onto it incrementally; the
legacy ``utils.get_html_from_url`` stays until they all are.
"""

from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """
    The outcome of a fetch. ``text`` is the response body; ``cookies`` and
    ``final_url`` are populated when the backend can provide them (browser /
    requests), empty/None otherwise.
    """

    url: str
    status: int
    text: str
    cookies: Dict[str, str] = field(default_factory=dict)
    final_url: Optional[str] = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        """Mimic ``requests``' raise_for_status for parsers that want it.

        Raises an ``HTTPError`` carrying a response whose ``status_code`` is set,
        so existing parser code that inspects ``e.response.status_code`` (e.g.
        the 404 -> MangaDoesNotExist checks) keeps working unchanged.
        """
        if not self.ok:
            import requests  # type: ignore

            resp = requests.Response()
            resp.status_code = self.status
            resp.url = self.url
            raise requests.exceptions.HTTPError(
                f"{self.status} for url {self.url}", response=resp
            )


@runtime_checkable
class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...


def fetch_soup(url: str, fetcher: Optional["Fetcher"] = None):
    """
    Fetch ``url`` and return a parsed ``BeautifulSoup`` (lxml).

    Defaults to ``CurlCffiFetcher`` (Chrome TLS/JA3 impersonation): it speaks the
    ``requests`` API but presents a real-browser fingerprint, so it transparently
    clears the fingerprint-based WAF blocking that trips plain ``requests`` while
    costing nothing extra for sites that don't care. ``RequestsFetcher`` remains
    available as a lightweight fallback. Raises ``requests.exceptions.HTTPError``
    on a non-2xx status (via ``FetchResult.raise_for_status``) so parsers keep
    their existing ``except HTTPError ... status_code == 404`` handling. This is
    the migration seam replacing ``utils.get_html_from_url(url)`` for plain-HTTP
    parsers.
    """
    import bs4

    if fetcher is None:
        fetcher = CurlCffiFetcher()
    result = fetcher.get(url)
    result.raise_for_status()
    return bs4.BeautifulSoup(result.text, features="lxml")


# ============================== http backends ============================


class CurlCffiFetcher:
    """
    ``curl_cffi`` GET with Chrome TLS/JA3 impersonation -- the default backend.

    Presents a real-browser TLS fingerprint, so it clears the fingerprint-based
    WAF/Cloudflare pre-response blocking that plain ``requests`` trips, while
    speaking the same ``requests``-style API. This is the same library and
    ``impersonate="chrome"`` mode the MangaFire parser already uses to pull page
    images past its CDN. Does not raise on HTTP error -- inspect
    ``FetchResult.ok`` / ``status`` or call ``raise_for_status()``.
    """

    def __init__(self, impersonate: str = "chrome") -> None:
        self.impersonate = impersonate

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> FetchResult:
        from curl_cffi import requests as creq  # type: ignore

        session = creq.Session(impersonate=self.impersonate)  # type: ignore[arg-type]
        resp = session.get(url, headers=headers, timeout=timeout)
        return FetchResult(
            url=url,
            status=resp.status_code,
            text=resp.text,
            cookies=dict(resp.cookies),
            final_url=resp.url,
        )


# HTTP statuses that are a definitive "no" -- retrying can't help and pounding
# them (esp. 403) in parallel is what gets the client IP throttled/banned.
_NO_RETRY_STATUSES = frozenset({401, 403, 404})


def download_image(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    max_tries: int = 5,
    timeout: int = 60,
    impersonate: str = "chrome",
    label: str = "image",
    backoff_base: float = 0.5,
    backoff_cap: float = 30.0,
) -> Optional[bytes]:
    """Download a page image via ``curl_cffi`` with Chrome TLS impersonation.

    This is the single shared CDN-facing download loop for image parsers
    (mangafire, mangabuddy) and the scaffold-generated ``page_data``. Image CDNs
    routinely reject non-browser TLS fingerprints, so we impersonate Chrome
    rather than use the plain ``requests`` downloader; ``cookies`` carries the
    browser session a site like MangaFire harvests during page-list capture.

    Retries up to ``max_tries`` times on a non-200 or a request exception, with
    **exponential backoff + jitter** between attempts (``backoff_base * 2**n``
    capped at ``backoff_cap``, plus up to half that as random jitter). CDN
    failures are usually transient/rate-limit, so waiting -- increasingly --
    between tries recovers far more pages than hammering instantly (which is what
    left chapters incomplete). No sleep is performed after the final attempt.
    Returns the raw image bytes on success, or ``None`` once exhausted. Callers
    own the post-download steps that genuinely differ between sites -- building
    the placeholder page on failure, image validation, and descrambling.
    """
    import random
    import time

    from curl_cffi import requests as creq  # type: ignore

    attempt = 0
    while attempt < max_tries:
        try:
            session = creq.Session(impersonate=impersonate)  # type: ignore[arg-type]
            if cookies:
                session.cookies.update(cookies)
            resp = session.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp.content
            if resp.status_code in _NO_RETRY_STATUSES:
                # A definitive refusal (forbidden / unauthorized / missing):
                # retrying cannot change the answer, and hammering a 403 in
                # parallel is exactly what gets the client IP rate-limited or
                # banned. Fail fast instead.
                logger.warning(f"{label} {resp.status_code} (not retrying): {url}")
                return None
            logger.warning(
                f"{label} attempt {attempt + 1}/{max_tries} "
                f"status {resp.status_code}: {url}"
            )
        except Exception as err:
            logger.warning(
                f"{label} attempt {attempt + 1}/{max_tries} failed: {url} - {err}"
            )
        attempt += 1
        if attempt < max_tries:
            delay = min(backoff_base * (2 ** (attempt - 1)), backoff_cap)
            delay += random.uniform(0, delay / 2)  # jitter to de-sync workers
            time.sleep(delay)
    logger.error(f"Download FAILED {label} at {url}")
    return None


class RequestsFetcher:
    """Plain ``requests`` GET (lightweight fallback). Does not raise on HTTP
    error -- inspect ``FetchResult.ok`` / ``status`` or call
    ``raise_for_status()``."""

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> FetchResult:
        import requests  # type: ignore

        resp = requests.get(url, headers=headers, timeout=timeout)
        return FetchResult(
            url=url,
            status=resp.status_code,
            text=resp.text,
            # RequestsCookieJar values are typed str|None; coerce to a clean
            # Dict[str, str] for FetchResult.cookies.
            cookies={k: v for k, v in resp.cookies.items() if v is not None},
            final_url=resp.url,
        )


class CloudscraperFetcher:
    """``cloudscraper`` GET -- clears Cloudflare's JS interstitial for sites
    that don't need a full browser."""

    def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> FetchResult:
        import cloudscraper  # type: ignore

        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, headers=headers, timeout=timeout)
        return FetchResult(
            url=url,
            status=resp.status_code,
            text=resp.text,
            cookies=dict(resp.cookies),
            final_url=resp.url,
        )


# ============================ browser backend ============================

# nodriver launch options used throughout the MangaFire work; kept here so all
# browser sessions are configured identically.
_BROWSER_KWARGS = dict(headless=False, sandbox=False, no_sandbox=True)


def _in_page_fetch_js(url: str) -> str:
    """Build the JS that fetches ``url`` from inside the page (credentialed,
    XHR header) and returns the response text. Pure -- unit-testable."""
    return (
        "(async () => {"
        f"  const r = await fetch({_json.dumps(url)}, "
        "    {credentials: 'include', "
        "     headers: {'X-Requested-With': 'XMLHttpRequest'}});"
        "  return await r.text();"
        "})()"
    )


# JS that forces lazy-loaded images to actually fetch: copy each img's
# ``data-src`` into ``src`` and scroll to the bottom so any scroll-triggered
# loaders fire. Returns the count touched. Pure (no interpolation).
_FORCE_LAZY_IMAGES_JS = (
    "(() => {"
    "  let n = 0;"
    "  document.querySelectorAll('img[data-src]').forEach(i => {"
    "    const ds = i.getAttribute('data-src');"
    "    if (ds && (!i.src || i.src.startsWith('data:'))) { i.src = ds; n++; }"
    "  });"
    "  window.scrollTo(0, document.body.scrollHeight);"
    "  return n;"
    "})()"
)


def _collect_img_urls_js(selector: str, attr: str) -> str:
    """JS returning the ordered, absolute image urls inside ``selector``,
    preferring ``attr`` then ``data-src`` then ``src`` (skipping ``data:``
    placeholders). Pure -- unit-testable."""
    return (
        "(() => {"
        f"  const c = document.querySelector({_json.dumps(selector)});"
        "  if (!c) return [];"
        "  return Array.from(c.querySelectorAll('img')).map(e =>"
        f"    e.getAttribute({_json.dumps(attr)}) || e.getAttribute('data-src')"
        "     || e.getAttribute('src') || '')"
        "    .filter(u => u && !u.startsWith('data:'))"
        "    .map(u => new URL(u, location.href).href);"
        "})()"
    )


def _count_elements_js(selector: str) -> str:
    """JS returning the number of elements matching ``selector``. Pure."""
    return f"document.querySelectorAll({_json.dumps(selector)}).length"


def _scroll_to_bottom_js(scroll_selector: Optional[str]) -> str:
    """JS that scrolls the window -- and optionally an inner container -- to the
    bottom, to trigger infinite-scroll / lazy loading. Pure."""
    inner = ""
    if scroll_selector:
        inner = (
            f"  const e = document.querySelector({_json.dumps(scroll_selector)});"
            "  if (e) e.scrollTop = e.scrollHeight;"
        )
    return (
        "(() => {"
        "  window.scrollTo(0, document.body.scrollHeight);"
        f"{inner}"
        "  return true;"
        "})()"
    )


def _make_marker_predicate(markers) -> Callable[[str], bool]:
    """A predicate matching any url that contains one of ``markers``. Pure."""
    markers = tuple(markers)

    def predicate(url: str) -> bool:
        return any(m in url for m in markers)

    return predicate


# Substrings (matched case-insensitively) that mark a Cloudflare / JS
# verification interstitial rather than real page content. Kept deliberately
# CF-specific so a real page that merely contains a common phrase isn't mistaken
# for a challenge.
_CHALLENGE_MARKERS = (
    "just a moment",  # CF interstitial <title>
    "checking your browser before",  # legacy CF "I'm Under Attack" mode
    "cf-browser-verification",  # CF challenge container id
    "challenge-platform",  # CF challenge script path
    "verifying you are human",  # Turnstile / managed challenge
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(html: str) -> bool:
    """True if ``html`` looks like a Cloudflare/JS verification interstitial
    rather than rendered page content (empty html counts as not-yet-loaded).

    Pure / unit-testable -- the polling in ``BrowserFetcher._get`` uses this to
    decide whether to keep waiting for the challenge to clear.
    """
    if not html or not html.strip():
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


class BrowserFetcher:
    """
    nodriver-backed fetcher for Cloudflare-protected, JS-heavy sites.

    Exposes three operations (all synchronous wrappers over nodriver's async
    API, mirroring how the MangaFire parser already drives it):

      * ``get(url)``                      -- navigate and return rendered HTML
      * ``fetch_json_in_page(a, b)``      -- navigate to ``a`` (clearance +
        cookies), then fetch known url ``b`` from inside the page
      * ``capture_xhr(url, predicate, ...)`` -- navigate to ``url``, optionally
        run ``trigger_js``, intercept the first XHR whose url matches
        ``predicate``, then re-fetch that url in-page; returns (text, cookies)
    """

    def __init__(self, wait: float = 5.0, timeout: float = 45.0) -> None:
        self.wait = wait
        self.timeout = timeout

    # -- sync wrappers -----------------------------------------------------

    def get(self, url: str) -> FetchResult:
        import asyncio

        text = asyncio.run(self._get(url))
        return FetchResult(url=url, status=200, text=text)

    def fetch_json_in_page(self, establish_url: str, fetch_url: str) -> str:
        import asyncio

        return asyncio.run(self._fetch_json_in_page(establish_url, fetch_url))

    def capture_xhr(
        self,
        url: str,
        predicate: Callable[[str], bool],
        trigger_js: Optional[str] = None,
        with_cookies: bool = False,
    ):
        import asyncio

        return asyncio.run(self._capture_xhr(url, predicate, trigger_js, with_cookies))

    def fetch_rendered_images(self, page_url: str, selector: str, attr: str = "src"):
        """Return ``[(url, bytes)]`` for every image inside ``selector`` on a
        browser-rendered page, in document order.

        For CDNs that serve images ONLY to the live browser session (the
        kakalot family: every HTTP client 403s even with cookies + Referer, and
        the images are cross-origin so an in-page ``fetch`` is CORS-blocked).
        We let the browser load the page (clearing the challenge), force the
        lazy ``data-src`` images to fetch, then read the bytes of the browser's
        OWN image responses via CDP ``Network.getResponseBody`` -- which isn't
        subject to CORS. A url whose body can't be read comes back as ``b""``.
        """
        import asyncio

        return asyncio.run(self._fetch_rendered_images(page_url, selector, attr))

    def get_after_scroll(
        self,
        url: str,
        count_selector: str,
        scroll_selector: Optional[str] = None,
        max_rounds: int = 40,
    ) -> FetchResult:
        """Return the page HTML after scrolling to load an infinite-scroll list.

        Some series pages lazy-load their chapter list as you scroll (no "show
        all" control). We navigate, clear the challenge, then repeatedly scroll
        the window (and the ``scroll_selector`` container) to the bottom until
        the count of ``count_selector`` elements stops growing -- so the
        returned HTML holds the FULL list, not just the first screen.
        """
        import asyncio

        return asyncio.run(
            self._get_after_scroll(url, count_selector, scroll_selector, max_rounds)
        )

    # -- async implementations --------------------------------------------

    async def _start(self):
        import tempfile
        from pathlib import Path

        import nodriver as nd

        profile = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
        return await nd.start(user_data_dir=profile, **_BROWSER_KWARGS)

    async def _get(self, url: str) -> str:
        browser = await self._start()
        try:
            page = await browser.get(url)
            return await self._wait_for_content(page, url)
        finally:
            browser.stop()

    async def _wait_for_content(self, page, url: str) -> str:
        """Poll the rendered HTML until it stops looking like a Cloudflare/JS
        challenge, up to ``self.timeout``.

        nodriver returns as soon as navigation settles, which on a CF-gated site
        is usually the "Just a moment..." interstitial -- so a flat single wait
        hands the challenge page back to the parser (-> no results / no chapter
        container). Instead we re-read the content every ``self.wait`` seconds
        and return as soon as it is real content. On timeout we return the last
        content (best effort) with a warning rather than hanging forever.
        """
        elapsed = 0.0
        content = ""
        while True:
            await page.wait(self.wait)
            elapsed += self.wait
            content = await page.get_content()
            if not _looks_like_challenge(content):
                return content
            if elapsed >= self.timeout:
                logger.warning(
                    f"browser still on a challenge page after {elapsed:.0f}s, "
                    f"returning it anyway: {url}"
                )
                return content
            logger.debug(f"challenge not cleared after {elapsed:.0f}s, waiting: {url}")

    async def _fetch_json_in_page(self, establish_url: str, fetch_url: str) -> str:
        import asyncio

        browser = await self._start()
        try:
            page = await browser.get(establish_url)
            await page.wait(self.wait)
            return await asyncio.wait_for(
                page.evaluate(_in_page_fetch_js(fetch_url), await_promise=True),
                timeout=self.timeout,
            )
        finally:
            browser.stop()

    async def _capture_xhr(
        self,
        url: str,
        predicate: Callable[[str], bool],
        trigger_js: Optional[str],
        with_cookies: bool,
    ):
        import asyncio

        from nodriver import cdp

        browser = await self._start()
        try:
            tab = await browser.get("about:blank")
            state: Dict[str, Optional[str]] = {"url": None}
            found = asyncio.Event()

            async def on_request(evt: cdp.network.RequestWillBeSent):
                if state["url"] is None and predicate(evt.request.url):
                    state["url"] = evt.request.url
                    found.set()

            async def on_response(evt: cdp.network.ResponseReceived):
                if state["url"] is None and predicate(evt.response.url):
                    state["url"] = evt.response.url
                    found.set()

            tab.add_handler(cdp.network.RequestWillBeSent, on_request)
            tab.add_handler(cdp.network.ResponseReceived, on_response)
            await tab.send(cdp.network.enable())

            await tab.get(url)
            if trigger_js:
                await tab.wait(3)
                await tab.evaluate(trigger_js)

            await asyncio.wait_for(found.wait(), timeout=self.timeout)
            captured_url = state["url"]
            # found.wait() only completes once a handler set state["url"], so it
            # is non-None here -- assert it for the type checker.
            assert captured_url is not None

            body = await asyncio.wait_for(
                tab.evaluate(_in_page_fetch_js(captured_url), await_promise=True),
                timeout=30,
            )

            cookies: Dict[str, str] = {}
            if with_cookies:
                try:
                    raw = await asyncio.wait_for(
                        tab.send(cdp.network.get_cookies()), timeout=10
                    )
                    cookies = {c.name: c.value for c in raw}
                except Exception as err:  # pragma: no cover - best effort
                    logger.warning(f"could not read cookies via CDP: {err}")

            return body, cookies
        finally:
            browser.stop()

    async def _fetch_rendered_images(
        self, page_url: str, selector: str, attr: str
    ):  # pragma: no cover - drives a real browser
        import asyncio
        import base64

        from nodriver import cdp

        browser = await self._start()
        try:
            tab = await browser.get("about:blank")
            # url -> CDP requestId, filled as the browser loads each image so we
            # can pull the bytes of its OWN responses (bypasses CORS).
            req_ids: Dict[str, object] = {}

            async def on_response(evt: cdp.network.ResponseReceived):
                try:
                    req_ids[evt.response.url] = evt.request_id
                except Exception:
                    pass

            tab.add_handler(cdp.network.ResponseReceived, on_response)
            await tab.send(cdp.network.enable())

            await tab.get(page_url)
            await self._wait_for_content(tab, page_url)
            # force lazy images to fetch, then let the responses arrive
            try:
                await tab.evaluate(_FORCE_LAZY_IMAGES_JS)
            except Exception as err:
                logger.debug(f"force-lazy failed (continuing): {err}")

            ordered: List[str] = []
            # poll until the image urls are present AND each has a response
            # recorded, or we hit the timeout (some pages stream slowly).
            waited = 0.0
            while waited < self.timeout:
                await tab.wait(self.wait)
                waited += self.wait
                ordered = list(
                    await tab.evaluate(_collect_img_urls_js(selector, attr)) or []
                )
                if ordered and all(u in req_ids for u in ordered):
                    break

            results: List[Tuple[str, bytes]] = []
            for url in ordered:
                data = b""
                rid = req_ids.get(url)
                if rid is not None:
                    try:
                        body, b64 = await asyncio.wait_for(
                            tab.send(cdp.network.get_response_body(rid)),
                            timeout=30,
                        )
                        data = base64.b64decode(body) if b64 else body.encode("latin-1")
                    except Exception as err:
                        logger.warning(f"could not read image body {url}: {err}")
                results.append((url, data))
            return results
        finally:
            browser.stop()

    async def _get_after_scroll(
        self,
        url: str,
        count_selector: str,
        scroll_selector: Optional[str],
        max_rounds: int,
    ) -> FetchResult:  # pragma: no cover - drives a real browser
        scroll_wait = 2.0  # per-round pause for the next lazy batch to load
        browser = await self._start()
        try:
            page = await browser.get(url)
            await self._wait_for_content(page, url)
            prev = -1
            unchanged = 0
            for _ in range(max_rounds):
                try:
                    await page.evaluate(_scroll_to_bottom_js(scroll_selector))
                except Exception as err:
                    logger.debug(f"scroll failed (continuing): {err}")
                await page.wait(scroll_wait)
                try:
                    count = int(await page.evaluate(_count_elements_js(count_selector)))
                except Exception:
                    count = prev
                if count <= prev:
                    unchanged += 1
                    if unchanged >= 3:  # no growth for 3 rounds -> list is fully loaded
                        break
                else:
                    unchanged = 0
                    prev = count
            logger.debug(f"scroll-to-load settled at {prev} items: {url}")
            return FetchResult(url=url, status=200, text=await page.get_content())
        finally:
            browser.stop()
