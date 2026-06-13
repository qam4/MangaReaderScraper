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

import atexit
import json as _json
import logging
import threading
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    Optional,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

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

# Statuses where the server is explicitly rate-limiting / temporarily down and
# may send a Retry-After telling us how long to wait.
_RATE_LIMIT_STATUSES = frozenset({429, 503})


def _parse_retry_after(value) -> Optional[float]:
    """Parse a ``Retry-After`` header (delta-seconds form) to float seconds.

    Returns ``None`` for a missing/non-numeric value (the HTTP-date form is not
    honored -- CDNs overwhelmingly use seconds) or a negative number. Pure /
    unit-tested. Tolerant of non-string inputs (returns None) so a mocked or
    odd header object can't blow up the download loop.
    """
    if not value:
        return None
    try:
        seconds = float(str(value).strip())
    except (ValueError, TypeError):
        return None
    return seconds if seconds >= 0 else None


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
    retry_after_cap: float = 120.0,
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

    On an explicit rate-limit status (429/503), if the server sends a
    ``Retry-After`` (seconds) we wait at least that long (capped at
    ``retry_after_cap``) instead of the shorter exponential delay -- the site is
    telling us how long to back off, so we listen. Returns the raw image bytes on
    success, or ``None`` once exhausted. Callers own the post-download steps that
    genuinely differ between sites -- building the placeholder page on failure,
    image validation, and descrambling.
    """
    import random
    import time

    from curl_cffi import requests as creq  # type: ignore

    attempt = 0
    while attempt < max_tries:
        retry_after: Optional[float] = None
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
            if resp.status_code in _RATE_LIMIT_STATUSES:
                # The server is explicitly throttling us; honor its Retry-After.
                retry_after = _parse_retry_after(
                    getattr(resp, "headers", {}).get("Retry-After")
                )
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
            if retry_after is not None:
                # the site told us how long to wait -- never wait LESS than that
                # (but cap it so a huge/hostile value can't stall the run)
                delay = min(max(delay, retry_after), retry_after_cap)
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

# nodriver launch options. The window is started OFF-SCREEN (huge negative
# position) so the headful browser never covers the CLI menu/prompts -- more
# reliable than CDP "minimized", which is a no-op on Windows. Off-screen keeps
# the page VISIBLE to the renderer (so scroll-driven lazy-load still works,
# unlike a minimized window). It's brought on-screen only for a manual challenge
# solve (see _show_browser_window).
_BROWSER_KWARGS = dict(
    headless=False,
    sandbox=False,
    no_sandbox=True,
    browser_args=["--window-position=-32000,-32000", "--window-size=1200,900"],
)


async def _show_browser_window(tab) -> None:  # pragma: no cover - real browser
    """Bring the off-screen browser window on-screen + maximize it, so the user
    can see and solve an interactive challenge. Best-effort (nodriver's
    high-level maximize binds the tab's target id and snaps to the monitor)."""
    try:
        await tab.maximize()
    except Exception as err:
        logger.debug(f"show window failed (continuing): {err}")


# Opt-in persistent browser profile. When this env var names a directory, the
# shared browser reuses it across runs, so a manually-solved Cloudflare challenge
# and its cf_clearance cookie SURVIVE between invocations (the "solve once, then
# continue" escape hatch). Safe because there is a single shared session (one
# Chrome instance -> no profile SingletonLock collision). Unset (default): a
# fresh throwaway profile per process, as before.
BROWSER_PROFILE_ENV = "MANGASCRAPER_BROWSER_PROFILE"


def _resolve_profile_dir() -> Path:
    """Resolve nodriver's ``user_data_dir``.

    Returns the persistent ``BROWSER_PROFILE_ENV`` directory (created if needed)
    when that env var is set, else a fresh throwaway temp dir. Pure-ish (env +
    mkdir, no browser) and unit-tested.
    """
    import os
    import tempfile
    from pathlib import Path

    persistent = os.environ.get(BROWSER_PROFILE_ENV)
    if persistent:
        path = Path(persistent).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="nodriver_profile_"))


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


# Phrases that only appear on an actual Cloudflare/JS interstitial WALL -- text a
# human would actually SEE and have to act on. We deliberately do NOT treat
# Cloudflare's always-on infrastructure (the ``challenge-platform`` / Turnstile
# SCRIPT, injected into EVERY page of a CF-fronted site, including fully-rendered
# ones) as a wall: matching it made ``_wait_for_content`` hang forever prompting
# for a manual solve that wasn't actually needed (e.g. a normal MangaFire series
# page). A real wall always carries one of these interstitial phrases.
_CHALLENGE_MARKERS = (
    "just a moment",  # CF interstitial <title>
    "checking your browser before",  # legacy CF "I'm Under Attack" mode
    "checking if the site connection is secure",
    "cf-browser-verification",  # CF challenge container id
    "verify you are human",  # Turnstile interactive checkbox label
    "verifying you are human",  # Turnstile / managed challenge
    "attention required",
    "enable javascript and cookies to continue",
)


def _looks_like_challenge(html: str) -> bool:
    """True if ``html`` looks like a Cloudflare/JS verification interstitial
    rather than rendered page content (empty html counts as not-yet-loaded).

    Matches only STRONG interstitial phrases (see ``_CHALLENGE_MARKERS``), NOT
    Cloudflare's always-on ``challenge-platform``/Turnstile script -- that script
    is present on fully-rendered pages too, and treating it as a challenge made
    the manual-solve wait hang forever on pages where no verification was shown.

    Pure / unit-testable -- the polling in ``BrowserFetcher._wait_for_content``
    uses this to decide whether to keep waiting for a challenge to clear.
    """
    if not html or not html.strip():
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


class _BrowserRuntime:
    """Owns ONE event loop (on a dedicated daemon thread) and ONE shared browser,
    so every ``BrowserFetcher`` call reuses a single session instead of launching
    a throwaway browser + event loop per call.

    Why a dedicated loop thread: nodriver drives Chrome over asyncio
    subprocesses, and an event loop (with its subprocess transports) is bound to
    the thread that runs it. By running ONE long-lived loop on a daemon thread
    and marshalling every browser coroutine onto it
    (``run_coroutine_threadsafe``), the browser is created and driven from a
    single, stable loop. That:

      * collapses the per-chapter browser explosion to ONE reusable session
        (download workers are THREADS now, so they can share it -- a browser
        can't cross a process boundary);
      * removes the per-call ``asyncio.run`` that closed a fresh loop each time
        and left nodriver's subprocess transports to be GC'd against a dead loop
        (the Windows ``I/O operation on closed pipe`` shutdown noise);
      * makes an opt-in persistent profile safe (single instance, no Chrome
        ``SingletonLock`` collision -- step (c)).

    Access is serialized with a lock because a single CDP session is not safe to
    drive concurrently. On Windows, ``new_event_loop()`` yields a
    ``ProactorEventLoop`` (the default policy since 3.8), which supports
    subprocesses on a non-main thread. Lazily started; stopped at interpreter
    exit. The loop-thread + serialization + lifecycle are unit-tested with plain
    coroutines; the browser-launching methods are real-browser-only.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()  # serializes whole browser ops
        self._start_lock = threading.Lock()  # guards loop-thread creation
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._browser = None

    def _ensure_loop(self) -> None:
        """Start the dedicated loop thread once (idempotent, thread-safe)."""
        import asyncio

        if self._loop is not None:
            return
        with self._start_lock:
            if self._loop is not None:
                return
            loop = asyncio.new_event_loop()

            def _run() -> None:
                asyncio.set_event_loop(loop)
                loop.run_forever()

            thread = threading.Thread(target=_run, name="browser-loop", daemon=True)
            thread.start()
            self._loop = loop
            self._thread = thread
            atexit.register(self.shutdown)

    def submit(self, coro):
        """Run an async browser op on the shared loop thread and block for its
        result, serialized against other ops.

        ``coro`` is created by the caller (a coroutine isn't bound to a loop
        until awaited). No timeout is imposed here: a browser op may legitimately
        block indefinitely (e.g. waiting for a human to solve a Cloudflare
        challenge), matching the old ``asyncio.run`` semantics; ops set their own
        internal timeouts where appropriate.
        """
        import asyncio
        import concurrent.futures

        self._ensure_loop()
        assert self._loop is not None  # _ensure_loop guarantees it
        with self._lock:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            try:
                # Poll with a short timeout rather than block forever, so a
                # Ctrl-C on the main thread can break a long/stuck browser op
                # (e.g. an unsolved challenge): future.result() with no timeout
                # is NOT interruptible by SIGINT on Windows.
                while True:
                    try:
                        return future.result(timeout=0.5)
                    except concurrent.futures.TimeoutError:
                        continue
            except KeyboardInterrupt:
                future.cancel()  # best effort; a running coro may not stop
                raise

    async def ensure_browser(self):  # pragma: no cover - launches a real browser
        """Start the shared browser once (on the loop thread) and reuse it."""
        if self._browser is None:
            self._browser = await self._launch()
        return self._browser

    async def _launch(self):  # pragma: no cover - launches a real browser
        import nodriver as nd

        profile = _resolve_profile_dir()
        # Opens off-screen via _BROWSER_KWARGS (--window-position), so no
        # post-launch minimize is needed; restored on-screen only for a manual
        # challenge solve. The profile is persistent iff BROWSER_PROFILE_ENV is
        # set (so a solved challenge survives across runs), else throwaway.
        return await nd.start(user_data_dir=profile, **_BROWSER_KWARGS)

    def shutdown(self) -> None:
        """Stop the shared browser and the loop thread (registered at exit).

        Idempotent. The browser is stopped ON the loop thread, followed by a
        short ``sleep`` so its subprocess transports close while the loop is
        still alive -- otherwise their ``__del__`` runs against a dead loop and
        prints the ``I/O operation on closed pipe`` warning.
        """
        import asyncio

        loop = self._loop
        if loop is None:
            return

        async def _close() -> None:
            if self._browser is not None:  # pragma: no cover - real browser
                try:
                    self._browser.stop()
                except Exception as err:
                    logger.debug(f"browser.stop() failed at shutdown: {err}")
                await asyncio.sleep(0.25)

        try:
            asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=10)
        except Exception as err:
            logger.debug(f"browser shutdown close failed: {err}")
        loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None
        self._browser = None


# Process-wide shared browser session (see _BrowserRuntime). All BrowserFetcher
# instances route through it, so there is at most ONE browser per process.
_RUNTIME = _BrowserRuntime()


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
        text = _RUNTIME.submit(self._get(url))
        return FetchResult(url=url, status=200, text=text)

    def fetch_json_in_page(self, establish_url: str, fetch_url: str) -> str:
        return _RUNTIME.submit(self._fetch_json_in_page(establish_url, fetch_url))

    def capture_xhr(
        self,
        url: str,
        predicate: Callable[[str], bool],
        trigger_js: Optional[str] = None,
        with_cookies: bool = False,
    ):
        return _RUNTIME.submit(
            self._capture_xhr(url, predicate, trigger_js, with_cookies)
        )

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
        return _RUNTIME.submit(
            self._get_after_scroll(url, count_selector, scroll_selector, max_rounds)
        )

    # -- async implementations --------------------------------------------

    async def _enable_focus_emulation(
        self, page
    ) -> None:  # pragma: no cover - real browser
        """Make the page report as focused/active WITHOUT stealing OS foreground.

        CDP ``setFocusEmulationEnabled`` + an 'active' lifecycle state make
        ``document.hasFocus()`` true so Cloudflare's PASSIVE (visibility-gated)
        challenge clears even while the window sits in the background. We do NOT
        raise the window over the user's terminal here -- that's reserved for an
        actual interactive solve (see ``_bring_to_front``), so a normal run
        doesn't cover the CLI menu/prompt. All best-effort.
        """
        from nodriver import cdp

        try:
            await page.send(cdp.emulation.set_focus_emulation_enabled(enabled=True))
        except Exception as err:
            logger.debug(f"setFocusEmulationEnabled failed (continuing): {err}")
        try:
            await page.send(cdp.page.set_web_lifecycle_state(state="active"))
        except Exception as err:
            logger.debug(f"setWebLifecycleState failed (continuing): {err}")

    async def _bring_to_front(self, page) -> None:  # pragma: no cover - real browser
        """Raise the browser window to the foreground. Called ONLY when the user
        must see and solve an interactive challenge -- otherwise we leave the
        window where it is so it doesn't cover the CLI during a normal run."""
        try:
            await page.bring_to_front()
        except Exception as err:
            logger.debug(f"bring_to_front failed (continuing): {err}")

    async def _get(self, url: str) -> str:  # pragma: no cover - real browser
        browser = await _RUNTIME.ensure_browser()
        page = await browser.get(url)
        return await self._wait_for_content(page, url)

    async def _wait_for_content(self, page, url: str, ready_selector=None) -> str:
        """Poll the rendered HTML until the page is ready, up to ``self.timeout``.

        nodriver returns as soon as navigation settles, which on a CF-gated site
        is usually the "Just a moment..." interstitial -- so a flat single wait
        hands the challenge page back to the parser (-> no results / no chapter
        container). Instead we re-read every ``self.wait`` seconds and return as
        soon as the page is ready:

        * with ``ready_selector`` -- as soon as that element EXISTS (a positive
          signal the real content rendered; more reliable than guessing the
          challenge is gone, and it waits out a slow challenge);
        * otherwise -- as soon as the HTML no longer looks like a challenge.

        On timeout we return the last content (best effort) with a warning
        rather than hanging forever.
        """
        # Make the page report focused (passive) so Cloudflare's visibility-gated
        # challenge can clear WITHOUT raising the window over the user's terminal.
        # We only bring it to the foreground if an interactive solve is needed.
        await self._enable_focus_emulation(page)
        elapsed = 0.0  # time the expected content has failed to appear (bounded)
        content = ""
        prompted = False
        while True:
            await page.wait(self.wait)
            content = await page.get_content()
            if ready_selector:
                try:
                    present = bool(
                        await page.evaluate(
                            f"!!document.querySelector({_json.dumps(ready_selector)})"
                        )
                    )
                except Exception:
                    present = False
                if present:
                    return content
            elif not _looks_like_challenge(content):
                return content
            # A Cloudflare challenge is on screen. An interactive Turnstile
            # ("Verify you are human") needs a real click we can't reliably
            # automate, so the user must complete it in the browser window. We do
            # NOT count this toward the timeout -- a human is working on it -- and
            # raise the window to the front (once) so they can see/solve it.
            if _looks_like_challenge(content):
                if not prompted:
                    # bring the off-screen window on-screen so the user can solve it
                    await _show_browser_window(page)
                    await self._bring_to_front(page)
                    logger.info(
                        "Cloudflare verification needed: click the 'Verify you "
                        "are human' checkbox in the browser window to continue. "
                        "Waiting for you..."
                    )
                    prompted = True
                continue
            # Not a challenge, but the expected content still hasn't appeared --
            # a load/selector problem, so bound it with the timeout.
            elapsed += self.wait
            if elapsed >= self.timeout:
                logger.warning(
                    f"page not ready (selector={ready_selector!r}) after "
                    f"{elapsed:.0f}s, returning anyway: {url}"
                )
                return content
            logger.debug(f"content not ready after {elapsed:.0f}s, waiting: {url}")

    async def _fetch_json_in_page(  # pragma: no cover - real browser
        self, establish_url: str, fetch_url: str
    ) -> str:
        import asyncio

        browser = await _RUNTIME.ensure_browser()
        page = await browser.get(establish_url)
        await page.wait(self.wait)
        return await asyncio.wait_for(
            page.evaluate(_in_page_fetch_js(fetch_url), await_promise=True),
            timeout=self.timeout,
        )

    async def _capture_xhr(  # pragma: no cover - real browser
        self,
        url: str,
        predicate: Callable[[str], bool],
        trigger_js: Optional[str],
        with_cookies: bool,
    ):
        import asyncio

        from nodriver import cdp

        browser = await _RUNTIME.ensure_browser()
        # The browser is shared/reused across calls, so this op runs in its OWN
        # tab (closed in finally): otherwise its CDP handlers + Network.enable
        # would leak onto a long-lived tab and bleed into later calls.
        tab = await browser.get("about:blank", new_tab=True)
        try:
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
            await self._enable_focus_emulation(tab)
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
            try:
                await tab.close()
            except Exception as err:
                logger.debug(f"tab.close() failed (continuing): {err}")

    async def _get_after_scroll(
        self,
        url: str,
        count_selector: str,
        scroll_selector: Optional[str],
        max_rounds: int,
    ) -> FetchResult:  # pragma: no cover - drives a real browser
        scroll_wait = 2.0  # per-round pause for the next lazy batch to load
        browser = await _RUNTIME.ensure_browser()
        page = await browser.get(url)
        await self._wait_for_content(page, url, ready_selector=count_selector)
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
                if unchanged >= 3:  # no growth for 3 rounds -> list fully loaded
                    break
            else:
                unchanged = 0
                prev = count
        logger.debug(f"scroll-to-load settled at {prev} items: {url}")
        return FetchResult(url=url, status=200, text=await page.get_content())
