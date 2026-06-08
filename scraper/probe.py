#!/usr/bin/env python3
"""
``scraper probe <url>`` -- capture real artifacts from a manga site so a parser
can be written against actual markup/endpoints instead of guesses
(refactoring-plan §4.1 / §4.3).

It drives a real browser (nodriver, headful so Cloudflare clears) and, for the
given URL:

  * logs **every ajax/API/JSON URL** the page fires (this alone often hands you
    the search / chapter-list / page-list API; some sites are API-backed, some
    are plain HTML -- the probe reports what it sees rather than assuming) and
    **dumps the JSON response bodies** of those calls so you can read the actual
    API shape instead of reverse-engineering Tailwind-class-soup markup, then
    **re-tries those endpoints with plain requests + curl_cffi** to tell you
    whether the API is reachable without a browser (``api_backends.txt``);
  * recommends a **fetch strategy** by trying the page with plain requests vs a
    browser: requests / nodriver-headless / nodriver-headful / nodriver-manual
    (interactive captcha needing user barge-in) / unknown;
  * heuristically surfaces candidate selectors and flags Cloudflare challenge
    walls vs. mere CF infrastructure;
  * **synthesizes a single advisory ``recommendation.txt``** -- it reads its own
    capture artifacts (ajax log, backend verdicts, captured bodies, page HTML)
    and distils them into one recommended approach (host(s) seen, whether an
    open JSON API was found, the suggested fetcher, and per-stage search /
    chapter-list / image mechanisms), so you read one summary first instead of
    cross-reading five files. It is *advisory* -- conclusions are phrased as
    suggestions to confirm against the live site, never as decisions;
  * dumps the rendered ``page.html`` + ``ajax_log.txt`` + ``candidates.txt`` +
    ``fetch_recommendation.txt`` + ``recommendation.txt`` into
    ``probe_out/<site>/`` (gitignored scratch; promote a curated subset to
    ``tests/test_files/<site>/`` by hand).

The analysis functions (``analyze_html``, ``detect_challenge``,
``compare_fetches``) are pure and unit-tested; the browser driving runs only
when invoked.

Usage:
  python -m scraper.probe https://mangafire.to/manga/<slug>
  python -m scraper.probe https://mangafire.to/home --search "naruto"
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from scraper.parsers._html import attr
from scraper.parsers.mangabuddy import (
    _images_from_chapter_payload,
    _next_data_from_html,
)

logger = logging.getLogger(__name__)

# a chapter-looking href fragment, e.g. /read/x/en/chapter-28.22 or chapter_55
_CHAPTER_HREF = re.compile(r"chapter[-_/]([\d.]+)", re.I)

# a chapter-looking link TEXT, e.g. "Chapter 700.6" / "Vol.72 Ch.700.6" / "Ch.5"
# -- catches sites whose chapter hrefs carry no "chapter" token (e.g. mangago's
# /read-manga/<slug>/mr/v72/c700.6/pg-1/), where the number lives in the text.
_CHAPTER_TEXT = re.compile(r"\bch(?:apter)?\.?\s*\d", re.I)

# a series/manga page href (NOT a chapter), used to pick the first real result
# off a search page: /manga/<slug>, /read-manga/<slug>, /title/, /series/, /comic/.
_SERIES_HREF = re.compile(r"/(?:manga|read-manga|title|series|comic)/", re.I)


def _absolutize(base_url: str, href: str) -> str:
    """Resolve a possibly-relative ``href`` against ``base_url``. Pure."""
    return urljoin(base_url, href)


def first_chapter_link(html: str, base_url: str) -> Optional[str]:
    """First chapter link of the MAIN chapter list on a series page, absolute
    url, or ``None``.

    The C5 chapters->images hop: the reader page is the first chapter's link.
    Naively taking the first chapter-looking anchor is wrong -- series pages are
    full of sidebar/"popular"/"latest updates" chapter links for OTHER mangas.
    The real chapter list is the one manga's own chapters, so they share a
    common series slug (``/manga/<slug>/chapter-..``) and dominate by count;
    sidebar entries are one-off different slugs. We therefore pick the MODAL
    series slug among chapter-looking links and return that group's first link.

    A link is chapter-looking when its href matches ``chapter-<n>`` OR its text
    reads "Chapter N" / "Vol.. Ch.." (the text rule catches sites whose chapter
    hrefs carry no "chapter" token, e.g. mangago). Pure / unit-tested against
    captured fixtures; the navigation itself is the browser's job.
    """
    soup = BeautifulSoup(html, "lxml")
    chapter_hrefs: List[str] = []
    for anchor in soup.find_all("a", href=True):
        href = attr(anchor, "href")
        if not href:
            continue
        if _CHAPTER_HREF.search(href) or _CHAPTER_TEXT.search(anchor.get_text()):
            chapter_hrefs.append(href)
    if not chapter_hrefs:
        return None
    # series slug = the href with its trailing /chapter... (and query) stripped
    counts: Counter = Counter(_series_key(h) for h in chapter_hrefs)
    modal_key, _ = counts.most_common(1)[0]
    for href in chapter_hrefs:
        if _series_key(href) == modal_key:
            return _absolutize(base_url, href)
    return _absolutize(base_url, chapter_hrefs[0])


def _series_key(href: str) -> str:
    """The series-identifying part of a chapter href: everything up to the
    chapter segment (so all of one manga's chapters share a key, while a
    different manga's do not). Pure."""
    low = href.lower()
    match = re.search(r"(chapter|/c\d|/mr/)", low)
    return href[: match.start()] if match else href


def _read_first_existing(out_dir: Path, names: List[str]) -> Optional[str]:
    """Return the text of the first of ``names`` that exists in ``out_dir``."""
    for name in names:
        path = out_dir / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return None


def run_multi(
    entry_url: str,
    query: Optional[str],
    out_base: Path,
    host: str,
    run_stage: Callable[[str, Path, Optional[str]], None],
) -> Dict[str, str]:
    """Orchestrate a single-entry, multi-stage probe (C5).

    Search-first chain (the entry is a HOME url + a query, so the series url is
    DERIVED, not supplied as a slug -- which is what kept going stale):

        search:   trigger the query on the entry page -> capture results,
                  then pick the first result -> the series url
        chapters: capture the series page -> pick the first chapter -> reader url
        images:   capture the reader page

    If no ``query`` is given the entry url is treated as the series page itself
    (the original "point at the manga url" mode), so it still does
    chapters -> images. ``run_stage(url, out_dir, search)`` performs one capture
    (the live browser work, injected so this orchestration is unit-testable; the
    navigation DECISIONS -- first_search_result_link / first_chapter_link -- are
    pure and fixture-tested). Returns the {stage: out_dir} map of stages run.

    Note: each stage drives its own browser pass; the result/chapter picks are
    advisory heuristics (see those helpers), so a wrong auto-pick is a coverage
    gap to confirm, not a silent error.
    """
    stages: Dict[str, str] = {}

    if query:
        search_dir = out_base / host / "search"
        run_stage(entry_url, search_dir, query)
        stages["search"] = str(search_dir)
        results_html = _read_first_existing(
            search_dir, ["search_page.html", "page.html"]
        )
        series_url = (
            first_search_result_link(results_html, entry_url) if results_html else None
        )
        if not series_url:
            print(
                "[probe] multi: no search result link found -- stopping after the "
                "search stage (inspect it and probe the series page directly)."
            )
            return stages
        print(f"[probe] multi: search -> series {series_url}")
    else:
        series_url = entry_url

    chapters_dir = out_base / host / "chapters"
    run_stage(series_url, chapters_dir, None)
    stages["chapters"] = str(chapters_dir)
    page_html = _read_first_existing(chapters_dir, ["page.html"])
    reader_url = first_chapter_link(page_html, series_url) if page_html else None
    if not reader_url:
        print(
            "[probe] multi: no chapter link found on the series page -- stopping "
            "after the chapters stage."
        )
        return stages
    print(f"[probe] multi: chapters -> reader {reader_url}")

    images_dir = out_base / host / "images"
    run_stage(reader_url, images_dir, None)
    stages["images"] = str(images_dir)
    return stages


def first_search_result_link(html: str, base_url: str) -> Optional[str]:
    """First search-result series link, as an absolute url, or ``None``.

    The C5 search-first entry: run a search, then the first result's link is the
    series (chapters) page -- so the series url is DERIVED from the query, not
    supplied as a slug (which is what kept going stale). A link counts as a
    result when its href matches a series path (``/manga/``, ``/read-manga/``,
    ``/title/`` ...) and is NOT itself a chapter link. First match in document
    order. Pure / unit-tested against captured search fixtures.
    """
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.find_all("a", href=True):
        href = attr(anchor, "href")
        if not href or _CHAPTER_HREF.search(href):
            continue
        if _SERIES_HREF.search(href):
            return _absolutize(base_url, href)
    return None


@dataclass
class ProbeReport:
    """Candidate selectors surfaced from a page's rendered HTML."""

    chapter_links: List[str] = field(default_factory=list)
    data_number_samples: List[str] = field(default_factory=list)
    data_src_samples: List[str] = field(default_factory=list)
    largest_img_container: Optional[str] = None
    img_container_count: int = 0
    challenge_markers: List[str] = field(default_factory=list)
    cloudflare_infra: List[str] = field(default_factory=list)
    page_size: int = 0

    @property
    def looks_like_challenge(self) -> bool:
        return bool(self.challenge_markers)

    def render(self) -> str:
        lines = ["# Candidate selectors (heuristic)\n"]
        lines.append(f"page size: {self.page_size} chars")
        if self.cloudflare_infra:
            lines.append(
                "behind Cloudflare (infra markers present: "
                f"{', '.join(self.cloudflare_infra)}) -- informational, not a block"
            )
        lines.append("")
        if self.challenge_markers:
            lines.append(
                "!! CHALLENGE WALL DETECTED -- the captured HTML looks like a "
                "challenge/interstitial, not the real content. Markers:"
            )
            for m in self.challenge_markers:
                lines.append(f"  {m}")
            lines.append("")
        lines.append(f"chapter-looking links: {len(self.chapter_links)}")
        for href in self.chapter_links[:10]:
            lines.append(f"  {href}")
        lines.append("")
        lines.append(f"elements with data-number: {len(self.data_number_samples)}")
        for s in self.data_number_samples[:10]:
            lines.append(f"  {s}")
        lines.append("")
        lines.append(
            f"elements with data-src (page images?): {len(self.data_src_samples)}"
        )
        for s in self.data_src_samples[:10]:
            lines.append(f"  {s}")
        lines.append("")
        if self.largest_img_container:
            lines.append(
                f"largest <img> cluster: <{self.largest_img_container}> "
                f"holding {self.img_container_count} images "
                f"(likely the page-image container)"
            )
        return "\n".join(lines) + "\n"


# Strong markers: phrases that only appear on an actual challenge/interstitial
# wall (the page is NOT the real content). Case-insensitive.
_STRONG_CHALLENGE_MARKERS = (
    "just a moment",
    "checking your browser",
    "checking if the site connection is secure",
    "verify you are human",
    "verifying you are human",
    "attention required",
    "enable javascript and cookies to continue",
)

# Weak markers: Cloudflare's always-on infrastructure, injected into EVERY page
# of a CF-fronted site (not just challenge walls). Their presence means "behind
# Cloudflare", NOT "blocked" -- so they only count as a challenge on a small page
# (a real wall is tiny; real content is large).
_WEAK_CHALLENGE_MARKERS = (
    "challenge-platform",
    "/cdn-cgi/challenge-platform",
    "__cf_chl",
    "cf_chl_opt",
    "turnstile",
)

# A real challenge wall is small; real content behind CF is large. Below this
# size, weak markers are treated as a likely wall.
_CHALLENGE_SIZE_HINT = 100_000


def detect_challenge(html: str, has_real_content: bool = False) -> List[str]:
    """
    Return challenge markers indicating the page is a Cloudflare/bot **wall**
    (not the real content). Pure -- no network.

    Strong interstitial phrases ("just a moment", "verify you are human") always
    count -- they only appear on an actual wall.

    Cloudflare's always-on infrastructure (challenge-platform script, __cf_chl,
    turnstile) is injected into EVERY page of a CF-fronted site, so it is a weak
    signal. It counts as a wall only when the page is BOTH:
      * small (``< _CHALLENGE_SIZE_HINT`` chars -- a real wall is tiny), AND
      * devoid of real content (``has_real_content`` is False).
    If the page carries real content (chapter links, data-number cards, page
    images), the browser already cleared the challenge and the CF markers are
    just the always-on script -- NOT a wall. This is what stops the
    false-positive where a fully-rendered MangaFire page (84 chapter links, but
    a ``turnstile`` script tag and < 100k chars) was reported as a challenge.
    """
    low = html.lower()
    strong = [m for m in _STRONG_CHALLENGE_MARKERS if m in low]
    if strong:
        return strong
    if has_real_content:
        return []
    if len(html) < _CHALLENGE_SIZE_HINT:
        weak = [m for m in _WEAK_CHALLENGE_MARKERS if m in low]
        if weak:
            return weak
    return []


def cloudflare_infrastructure(html: str) -> List[str]:
    """Weak CF markers present regardless of page size -- informational only
    ('this site sits behind Cloudflare'), not a block signal."""
    low = html.lower()
    return [m for m in _WEAK_CHALLENGE_MARKERS if m in low]


@dataclass
class FetchComparison:
    """
    Outcome of fetching a page two ways (plain requests vs a real browser), used
    to decide which fetcher a parser should declare for that page.
    """

    requests_status: Optional[int] = None
    requests_challenge: bool = False
    requests_len: int = 0
    requests_error: Optional[str] = None
    browser_challenge: bool = False
    browser_len: int = 0
    # does the rendered (browser) HTML have substantially more content than the
    # raw requests HTML? -> the page is JS-rendered / dynamic
    dynamic: bool = False

    def strategy(self) -> str:
        """
        Pick the cheapest fetch strategy that works for this page, as a stable
        token. The ladder, cheapest -> most invasive:

          requests          - plain requests is enough (static HTML, no block)
          cloudscraper       - requests blocked/non-200 but no full browser
                               needed (try the CloudscraperFetcher tier first)
          nodriver-headless  - needs a real browser engine, but content renders
                               without a visible window / interaction
          nodriver-headful   - needs a visible browser (challenge clears with a
                               real window but no human action)
          nodriver-manual    - an interactive captcha remains; needs the user to
                               barge in and solve it in a headful window
          unknown            - couldn't fetch either way (network/dns error)
        """
        if self.requests_error and self.browser_len == 0:
            return "unknown"
        # plain requests got real content, no challenge -> cheapest tier wins
        if (
            not self.requests_error
            and self.requests_status == 200
            and not self.requests_challenge
            and not self.dynamic
        ):
            return "requests"
        # browser still shows a challenge wall -> a human must solve it
        if self.browser_challenge:
            return "nodriver-manual"
        # requests blocked/challenged but the browser cleared it cleanly
        if self.requests_challenge or self.requests_status not in (200, None):
            return "nodriver-headless"
        # requests fine status but content is JS-rendered -> needs a browser
        if self.dynamic:
            return "nodriver-headless"
        # requests errored outright but the browser worked
        if self.requests_error:
            return "nodriver-headless"
        return "requests"

    def recommend(self) -> str:
        """Human-readable recommendation for the chosen strategy."""
        return {
            "requests": "fetch_soup / RequestsFetcher (plain requests is enough)",
            "cloudscraper": "CloudscraperFetcher (requests blocked; no full browser needed)",
            "nodriver-headless": (
                "BrowserFetcher headless (needs a real browser engine; "
                "renders without a visible window)"
            ),
            "nodriver-headful": (
                "BrowserFetcher headful (needs a visible browser window to clear "
                "the challenge, but no human action)"
            ),
            "nodriver-manual": (
                "BrowserFetcher headful + USER BARGE-IN (an interactive captcha "
                "remains; the user must solve it in the window, then scraping "
                "continues with the cleared session)"
            ),
            "unknown": "could not fetch the page either way (network/dns error)",
        }[self.strategy()]

    def render(self, label: str) -> str:
        lines = [f"# Fetch strategy: {label}\n"]
        if self.requests_error:
            lines.append(f"requests: ERROR {self.requests_error}")
        else:
            lines.append(
                f"requests: status={self.requests_status} "
                f"len={self.requests_len} "
                f"challenge={'YES' if self.requests_challenge else 'no'}"
            )
        lines.append(
            f"browser:  len={self.browser_len} "
            f"challenge={'YES' if self.browser_challenge else 'no'}"
        )
        lines.append(
            f"dynamic (browser >> requests content): {'YES' if self.dynamic else 'no'}"
        )
        lines.append("")
        lines.append(f"-> strategy: {self.strategy()}")
        lines.append(f"-> {self.recommend()}")
        return "\n".join(lines) + "\n"


def compare_fetches(
    requests_html: Optional[str],
    requests_status: Optional[int],
    browser_html: str,
    requests_error: Optional[str] = None,
    dynamic_ratio: float = 1.5,
) -> FetchComparison:
    """
    Build a FetchComparison from the two fetch outcomes. Pure -- no network.

    ``dynamic`` is true when the browser-rendered HTML is at least
    ``dynamic_ratio``x larger than the plain-requests HTML (a strong sign the
    page builds its content with JS, so requests alone would miss it).
    """
    cmp = FetchComparison(
        requests_status=requests_status,
        requests_error=requests_error,
        browser_len=len(browser_html),
        browser_challenge=bool(detect_challenge(browser_html)),
    )
    if requests_html is not None:
        cmp.requests_len = len(requests_html)
        cmp.requests_challenge = bool(detect_challenge(requests_html))
        if cmp.requests_len and len(browser_html) > cmp.requests_len * dynamic_ratio:
            cmp.dynamic = True
    return cmp


def site_name_from_url(url: str) -> str:
    """Derive a fixtures folder name from a URL host: www.mangabuddy.com -> mangabuddy."""
    host = urlparse(url).netloc.lower()
    host = host.split(":")[0]
    parts = [p for p in host.split(".") if p not in ("www",)]
    # drop the TLD: ['mangabuddy', 'com'] -> 'mangabuddy'
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "site"


def stage_from_url(url: str, searching: bool = False) -> str:
    """Classify a probe URL into a parser-stage subfolder name.

    A site serves each parser stage from a different page, and probing several
    pages of one site into a single ``probe_out/<host>/`` folder silently
    overwrites the shared summary files. Deriving a per-stage subfolder from the
    URL PATH keeps the three captures apart by default (no ``--site`` needed):

      * a ``--search`` run                       -> ``"search"``
      * a reader/chapter page (``/read/``,
        ``chapter-..`` / ``chapter_..`` / ``/chapter/``) -> ``"images"``
      * a series/manga page (``/manga/``,
        ``/title/``, ``/series/``, ``/comic/``)  -> ``"chapters"``
      * anything else (home, unknown)            -> ``"home"``

    Pure -- no IO. ``searching`` takes precedence because the search action is
    what that run is capturing regardless of which page it starts on.
    """
    if searching:
        return "search"
    path = urlparse(url).path.lower()
    if "/read/" in path or "/chapter/" in path or re.search(r"chapter[-_]", path):
        return "images"
    if any(seg in path for seg in ("/manga/", "/title/", "/series/", "/comic/")):
        return "chapters"
    return "home"


def analyze_html(html: str) -> ProbeReport:
    """
    Surface candidate selectors from rendered HTML. Pure -- no network.

    Heuristics (refactoring-plan §4.1):
      - links whose href looks like a chapter (``chapter-<num>``)
      - elements carrying ``data-number`` (chapter cards) or ``data-src`` (lazy
        page images)
      - the element holding the largest cluster of ``<img>`` (page container)
    """
    soup = BeautifulSoup(html, "lxml")
    report = ProbeReport()
    report.page_size = len(html)
    report.cloudflare_infra = cloudflare_infrastructure(html)

    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = attr(a, "href")
        if _CHAPTER_HREF.search(href) and href not in seen_links:
            seen_links.add(href)
            report.chapter_links.append(href)

    for tag in soup.find_all(attrs={"data-number": True}):
        report.data_number_samples.append(str(tag)[:120])

    for tag in soup.find_all(attrs={"data-src": True}):
        report.data_src_samples.append(attr(tag, "data-src"))

    # largest <img> cluster: the parent tag holding the most <img> children
    parent_counts: Counter = Counter()
    for img in soup.find_all("img"):
        if img.parent is not None:
            parent_counts[id(img.parent)] += 1
    if parent_counts:
        best_id, count = parent_counts.most_common(1)[0]
        for img in soup.find_all("img"):
            if img.parent is not None and id(img.parent) == best_id:
                report.largest_img_container = img.parent.name
                report.img_container_count = count
                break

    # A page carrying real content (chapter links, data-number cards, or a
    # cluster of page images) means the browser cleared any CF challenge -- so
    # weak CF infra markers must NOT be reported as a wall (the C9 fix).
    has_real_content = bool(
        report.chapter_links
        or report.data_number_samples
        or report.data_src_samples
        or report.img_container_count > 1
    )
    report.challenge_markers = detect_challenge(html, has_real_content)

    return report


def _element_selector(tag) -> str:
    """A short, readable selector for a single element: ``tag#id.class1.class2``
    (id and up to a couple of classes). Pure."""
    if tag is None or getattr(tag, "name", None) is None:
        return "?"
    sel = tag.name
    tag_id = tag.get("id")
    if isinstance(tag_id, str) and tag_id:
        sel += f"#{tag_id}"
    classes = tag.get("class")
    if isinstance(classes, str):
        classes = classes.split()
    if classes:
        sel += "".join(f".{c}" for c in classes[:3])
    return sel


def _ancestor_path(tag, depth: int = 3) -> str:
    """``parent > child > tag`` selector path up to ``depth`` ancestors. Pure."""
    chain = []
    cur = tag
    for _ in range(depth + 1):
        if cur is None or getattr(cur, "name", None) in (None, "[document]"):
            break
        chain.append(_element_selector(cur))
        cur = cur.parent
    return " > ".join(reversed(chain))


@dataclass
class FoundMatch:
    """One place a search string was found in the HTML."""

    where: str  # "text" or an attribute name like "href"
    selector: str  # the element itself, e.g. a.chico
    path: str  # ancestor path, e.g. table.listing > tr > td > a.chico
    snippet: str  # the surrounding text/value, trimmed


def find_text(html: str, needle: str, limit: int = 25) -> List[FoundMatch]:
    """
    Locate every element whose visible text or an attribute value contains
    ``needle``, reporting the element's selector + ancestor path. Pure -- no
    network.

    This is the manual-inspection accelerator: you give a string you can SEE on
    the page (a chapter number, a title, a slug), and it tells you exactly which
    element + container it lives in -- so you can write the parser selector
    without scrolling through hundreds of KB of HTML. It does not guess which
    match is "the right one"; you interpret the (usually short) list.
    """
    soup = BeautifulSoup(html, "lxml")
    low = needle.lower()
    matches: List[FoundMatch] = []
    seen: set = set()

    # 1) attribute values containing the needle (href, src, data-*, alt, ...)
    for tag in soup.find_all(True):
        for name, value in tag.attrs.items():
            val = " ".join(value) if isinstance(value, list) else str(value)
            if low in val.lower():
                key = (id(tag), name)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(
                    FoundMatch(
                        where=name,
                        selector=_element_selector(tag),
                        path=_ancestor_path(tag),
                        snippet=val[:80],
                    )
                )
                if len(matches) >= limit:
                    return matches

    # 2) the innermost element whose direct text contains the needle
    for tag in soup.find_all(True):
        direct = "".join(c for c in tag.find_all(string=True, recursive=False)).strip()
        if direct and low in direct.lower():
            key = (id(tag), "text")
            if key in seen:
                continue
            seen.add(key)
            matches.append(
                FoundMatch(
                    where="text",
                    selector=_element_selector(tag),
                    path=_ancestor_path(tag),
                    snippet=direct[:80],
                )
            )
            if len(matches) >= limit:
                break
    return matches


def render_matches(needle: str, matches: List[FoundMatch]) -> str:
    lines = [f"# Locations of {needle!r}  ({len(matches)} match(es))\n"]
    if not matches:
        lines.append("(not found -- check the exact string, or open page.html)")
        return "\n".join(lines) + "\n"
    for m in matches:
        lines.append(f"[{m.where}] {m.path}")
        lines.append(f"    {m.snippet!r}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Phase 1 -- recommendation synthesis: read the probe's own capture artifacts
# (ajax log, api_backends verdicts, captured bodies, page HTML) and distil them
# into ONE recommended approach, so the developer does not have to cross-read
# five files to choose a fetcher and a per-stage strategy. The output is
# deliberately *advisory* -- it phrases conclusions as suggestions to confirm
# against the live site, never as decisions (Req 1.5, Property 6). The dataclass
# ``render()`` below is pure string building (Property 7); ``_probe`` is the
# only thing that writes it to ``recommendation.txt``.
# ---------------------------------------------------------------------------


@dataclass
class StagePlan:
    """A suggested mechanism + fetcher for one parser stage.

    One of the three stages a parser implements -- ``"search"``,
    ``"chapters"``, or ``"images"``. ``mechanism`` is human-readable text for
    the candidate endpoint/source (e.g. ``api.mangak.io/titles/search`` or
    ``embedded in page __NEXT_DATA__``); ``fetcher`` is the suggested fetcher
    token (``"curl_cffi"`` | ``"browser"`` | ``"requests"`` | ``"unknown"``);
    ``note`` carries optional gotcha hints (e.g. embedded-vs-API). Advisory
    only -- the developer confirms against the live site.
    """

    name: str
    mechanism: str
    fetcher: str
    note: str = ""


@dataclass
class Recommendation:
    """A synthesized, advisory summary of how to approach a probed site.

    Ties together the host(s) seen, whether an open JSON API was found, the
    recommended default fetcher, and the per-stage suggestions (search /
    chapter-list / images). :meth:`render` produces the ``recommendation.txt``
    text; it surfaces every field (Req 1.1) and frames the whole thing as
    suggestions to confirm, not decisions (Req 1.5, Property 6). Pure -- the
    dataclass carries no IO (Property 7).
    """

    hosts: List[str] = field(default_factory=list)
    api_open: bool = False
    default_fetcher: str = "unknown"
    stages: List[StagePlan] = field(default_factory=list)

    def render(self) -> str:
        """Render the advisory recommendation text. Pure -- string building
        only, robust to empty hosts/stages (never raises)."""
        lines = [
            "# Recommended approach (advisory)\n",
            "These are SUGGESTIONS synthesized from the captured artifacts, not "
            "decisions -- confirm each against the live site before writing the "
            "parser.",
            "",
            "host(s) seen: " + (", ".join(self.hosts) if self.hosts else "(none seen)"),
            f"open JSON API found: {'yes' if self.api_open else 'no'}",
            f"suggested default fetcher: {self.default_fetcher}",
            "",
            "per-stage suggestions:",
        ]
        if not self.stages:
            lines.append(
                "  (no stage candidates identified -- inspect the captures by hand)"
            )
        else:
            for stage in self.stages:
                lines.append("")
                lines.append(f"[{stage.name}] {stage.mechanism}")
                lines.append(f"    suggested fetcher: {stage.fetcher}")
                if stage.note:
                    lines.append(f"    note: {stage.note}")
        return "\n".join(lines) + "\n"


# Image file extensions a page-image URL plausibly ends in. Used to recognise a
# captured body that is an array of image URLs (an API-served image list).
_IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png", ".gif", ".avif", ".bmp")

# How deep to look inside a captured JSON body for an array of image URLs. Real
# API/embedded payloads are shallow; the bound keeps the search terminating on
# pathological input (matches the bounded-traversal style of Phase 2).
_MAX_IMAGE_SEARCH_DEPTH = 25


def _is_working_backend(verdict: Optional[str]) -> bool:
    """True if a backend ``verdict`` means a non-browser client got the API.

    The ``api_backends`` map records, per endpoint, the cheapest backend that
    returned real JSON, or ``"blocked"`` when none did. Any non-empty verdict
    other than ``"blocked"`` therefore means *some* HTTP client worked (we stay
    tolerant of undocumented tokens rather than hard-coding the two we expect).
    Pure -- no IO.
    """
    return bool(verdict) and verdict != "blocked"


def _pick_default_fetcher(
    api_backends: Dict[str, str],
    api_bodies: List[Tuple[str, str]],
) -> str:
    """Suggest the cheapest fetcher that worked across the data endpoints.

    Ranked cheapest -> most invasive: ``requests`` < ``curl_cffi`` < ``browser``
    (Req 1.2, 1.3). If any endpoint answered plain ``requests`` we suggest that;
    else if any answered ``curl_cffi`` we suggest that; else if every endpoint
    is blocked but the browser captured a body we suggest ``browser`` (Req 1.3);
    else ``unknown``. Tolerant of undocumented working verdicts (surfaced as-is).
    Pure -- no IO.
    """
    verdicts = list(api_backends.values())
    if any(v == "requests" for v in verdicts):
        return "requests"
    if any(v == "curl_cffi" for v in verdicts):
        return "curl_cffi"
    for verdict in verdicts:
        if _is_working_backend(verdict):
            return verdict
    if api_bodies:
        return "browser"
    return "unknown"


def _verdict_to_fetcher(verdict: Optional[str], body_exists: bool) -> str:
    """Map one endpoint's ``api_backends`` verdict to a StagePlan fetcher token.

    ``"requests"`` -> ``"requests"``, ``"curl_cffi"`` -> ``"curl_cffi"``; a
    blocked/missing verdict becomes ``"browser"`` when a body was captured for
    that endpoint (so a browser is required, Req 1.3) and ``"unknown"`` when not
    even the browser got it. Undocumented working tokens pass through. Pure.
    """
    if verdict == "requests":
        return "requests"
    if verdict == "curl_cffi":
        return "curl_cffi"
    if _is_working_backend(verdict):
        return verdict  # type: ignore[return-value]  # non-None per the guard
    return "browser" if body_exists else "unknown"


def _url_haystack(url: str) -> str:
    """Lower-cased ``host + path`` of a URL, for keyword pattern-matching. Pure."""
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path).lower()


def _find_endpoint(
    urls: List[str],
    include: Tuple[str, ...],
    exclude: Tuple[str, ...] = (),
) -> Optional[str]:
    """First URL whose host+path contains an ``include`` keyword and none of the
    ``exclude`` keywords.

    ``include`` is checked in priority order: every URL is tried against the
    first keyword before moving on to the next, so a more-specific keyword
    (``chapter``) wins over a looser fallback (``titles``). ``exclude`` keeps the
    looser fallback from latching onto a sibling endpoint (e.g. ``/titles/search``
    is not the chapter list even though it contains ``titles``). Pure -- no IO.
    """
    for keyword in include:
        for url in urls:
            hay = _url_haystack(url)
            if keyword in hay and not any(bad in hay for bad in exclude):
                return url
    return None


def _hosts_seen(urls: List[str]) -> List[str]:
    """Distinct hosts across ``urls``, in first-seen order. Pure -- no IO."""
    hosts: List[str] = []
    for url in urls:
        netloc = urlparse(url).netloc
        if netloc and netloc not in hosts:
            hosts.append(netloc)
    return hosts


def _endpoint_stage(
    name: str,
    url: Optional[str],
    api_backends: Dict[str, str],
    body_urls: Set[str],
) -> StagePlan:
    """Build a StagePlan for an endpoint-backed stage (search / chapters).

    ``mechanism`` is the matched endpoint URL (or a "(no … endpoint seen)" note
    when nothing matched); ``fetcher`` is that endpoint's backend verdict mapped
    to a fetcher token. Advisory only. Pure -- no IO.
    """
    if url is None:
        return StagePlan(
            name=name, mechanism=f"(no {name} endpoint seen)", fetcher="unknown"
        )
    fetcher = _verdict_to_fetcher(api_backends.get(url), url in body_urls)
    return StagePlan(name=name, mechanism=url, fetcher=fetcher)


def _looks_like_image_url(value: object) -> bool:
    """True if ``value`` is a string whose URL path ends in an image extension
    (``.webp`` / ``.jpg`` / ``.png`` / ...). Pure -- no IO."""
    if not isinstance(value, str):
        return False
    return urlparse(value).path.lower().endswith(_IMAGE_EXTS)


def _safe_json(body: str) -> object:
    """Parse a captured body to JSON, or ``None`` if it does not parse.

    Reuses :func:`looks_like_json` as a cheap pre-check, then ``json.loads``
    inside a guard so a non-JSON (e.g. Cloudflare HTML) body is skipped rather
    than raising (design "Error handling"). Pure -- no IO.
    """
    if not looks_like_json(body):
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _find_image_array(obj: object, depth: int = 0) -> Optional[List[str]]:
    """Return the first array of image-looking URL strings found within ``obj``.

    Walks dicts/lists to a bounded depth; a list whose string elements are all
    image URLs is taken to be a page-image list (the API-served images case,
    Req 1.4). Returns ``None`` when no such array is present. Pure -- no IO.
    """
    if depth > _MAX_IMAGE_SEARCH_DEPTH:
        return None
    if isinstance(obj, list):
        strings = [x for x in obj if isinstance(x, str)]
        if strings and all(_looks_like_image_url(x) for x in strings):
            return strings
        for child in obj:
            found = _find_image_array(child, depth + 1)
            if found:
                return found
    elif isinstance(obj, dict):
        for child in obj.values():
            found = _find_image_array(child, depth + 1)
            if found:
                return found
    return None


def _images_stage(
    api_backends: Dict[str, str],
    api_bodies: List[Tuple[str, str]],
    body_urls: Set[str],
    page_html: Optional[str],
) -> StagePlan:
    """Decide the suggested image mechanism for the ``images`` stage (Req 1.4).

    Three cases, in order of preference:
      1. a captured body is an array of image-looking URLs -> API-served images
         (mechanism = that endpoint, fetcher = its backend verdict);
      2. else the page's embedded ``__NEXT_DATA__`` carries an images array ->
         images are EMBEDDED in the page, NOT API-served; the page is fetched
         curl_cffi-first with a browser fallback (mirrors the mangabuddy parser);
      3. else no image source could be identified -> suggest manual inspection.
    Advisory only. Pure -- no IO.
    """
    # 1. API-served: a captured body that is (or contains) an image-URL array.
    for url, body in api_bodies:
        images = _find_image_array(_safe_json(body))
        if images:
            fetcher = _verdict_to_fetcher(api_backends.get(url), url in body_urls)
            note = (
                f"a captured body returns an array of image-looking URLs "
                f"({len(images)} seen) -- looks API-served; confirm the JSON path"
            )
            return StagePlan(name="images", mechanism=url, fetcher=fetcher, note=note)

    # 2. Embedded in the server-rendered page payload (Next.js __NEXT_DATA__).
    if page_html:
        next_data = _next_data_from_html(page_html)
        if next_data is not None and _images_from_chapter_payload(next_data):
            note = (
                "images appear EMBEDDED in the page __NEXT_DATA__ "
                "(pageProps.initialChapter.images), NOT API-served -- suggest "
                "fetching the page (curl_cffi first, browser fallback) and "
                "reading the embedded images path, like the mangabuddy parser"
            )
            return StagePlan(
                name="images",
                mechanism="embedded in page __NEXT_DATA__",
                fetcher="curl_cffi",
                note=note,
            )

    # 3. Nothing recognisable.
    return StagePlan(
        name="images",
        mechanism="(no image source identified)",
        fetcher="unknown",
        note=(
            "no image-list endpoint or embedded images array was seen -- "
            "inspect page.html and the captured api_*.json bodies by hand"
        ),
    )


def synthesize_recommendation(
    ajax_urls: List[str],
    api_backends: Dict[str, str],
    api_bodies: List[Tuple[str, str]],
    page_html: Optional[str],
) -> Recommendation:
    """Distil the probe's capture artifacts into one advisory Recommendation.

    Inputs (all already produced by a capture pass, here as in-memory values):
      * ``ajax_urls``    -- every API/AJAX URL the page fired;
      * ``api_backends`` -- endpoint URL -> cheapest working backend verdict
        (``"requests"`` | ``"curl_cffi"`` | ... ) or ``"blocked"``;
      * ``api_bodies``   -- captured ``(url, body_text)`` response bodies;
      * ``page_html``    -- the rendered page HTML (or ``None``).

    Decisions (Req 1.2-1.4), all phrased as suggestions in the rendered text
    (Req 1.5, Property 6):
      * ``api_open``        -- any endpoint a non-browser client reached;
      * ``default_fetcher`` -- the cheapest backend that worked, browser only as
        a last resort when bodies exist (Req 1.3);
      * ``stages``          -- a StagePlan each for search, chapters and images,
        from pattern-matched endpoints and the captured bodies / page payload.

    Robust to empty/partial inputs (missing artifacts -> empty, never raises;
    ``page_html=None`` and unparseable bodies are tolerated). Pure -- no
    network/browser/file IO (Req 1.6, Property 7).
    """
    body_urls: Set[str] = {url for url, _body in api_bodies}
    candidate_urls = list(
        dict.fromkeys(
            list(ajax_urls) + [url for url, _body in api_bodies] + list(api_backends)
        )
    )

    search_url = _find_endpoint(candidate_urls, ("search",))
    chapters_url = _find_endpoint(
        candidate_urls, ("chapter", "titles"), exclude=("search",)
    )

    stages = [
        _endpoint_stage("search", search_url, api_backends, body_urls),
        _endpoint_stage("chapters", chapters_url, api_backends, body_urls),
        _images_stage(api_backends, api_bodies, body_urls, page_html),
    ]

    return Recommendation(
        hosts=_hosts_seen(candidate_urls),
        api_open=any(_is_working_backend(v) for v in api_backends.values()),
        default_fetcher=_pick_default_fetcher(api_backends, api_bodies),
        stages=stages,
    )


# ---------------------------------------------------------------------------
# Phase 2 -- map-by-example: locate human-supplied values inside captured JSON.
# These are the JSON counterpart to ``find_text`` (which works on HTML): the
# developer gives a value they can SEE on the page and the locator reports the
# JSON path(s) it lives at, so the title/slug/chapter fields can be identified
# without scrolling thousands of lines. Pure -- no network/browser/file IO.
# ---------------------------------------------------------------------------

# Cap how deep we walk nested JSON. Real API/embedded payloads are shallow; a
# bound keeps the traversal terminating on pathological input and satisfies the
# "bounded depth" contract (Req 2.6). Cyclic-free JSON from json.loads cannot
# exceed this in practice.
_MAX_JSON_DEPTH = 50


@dataclass
class PathMatch:
    """One JSON path whose leaf matched a target value, with the match kind.

    ``path`` is a dotted/indexed locator (e.g. ``data.items[0].name``) that
    resolves back to ``leaf`` via :func:`get_by_path`. ``kind`` is one of:

      * ``"exact"``      -- a string leaf equal to the target.
      * ``"substring"``  -- a string leaf that *contains* the target.
      * ``"numeric"``    -- leaf and target denote the same number (numeric leaf
        vs numeric string, or two differently-formatted numeric strings).
      * ``"path-prefix"``-- a URL-ish leaf equal to the target apart from a
        leading path separator (e.g. ``/naruto`` vs ``naruto``).
    """

    path: str
    leaf: object
    kind: str


def _to_number(x: object) -> Optional[float]:
    """Parse ``x`` to a float if it denotes a number, else ``None``.

    Booleans are rejected (``True``/``False`` are ``int`` subclasses in Python
    but never a "number" a developer would map a chapter value onto). Pure.
    """
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.strip())
        except ValueError:
            return None
    return None


def _numeric_equal(leaf: object, value: str) -> bool:
    """True if ``leaf`` and the target string denote the same number. Pure."""
    a = _to_number(leaf)
    b = _to_number(value)
    return a is not None and b is not None and a == b


def _is_path_prefix(leaf: str, value: str) -> bool:
    """True if ``leaf`` equals ``value`` apart from leading path separator(s),
    e.g. ``/naruto`` vs ``naruto`` (Req 2.4). Pure."""
    return leaf != value and leaf.lstrip("/") == value.lstrip("/")


def _classify_leaf(leaf: object, value: str) -> Optional[str]:
    """Return the most specific match kind for ``leaf`` against the target
    ``value``, or ``None`` if it does not match.

    Precedence (most specific first), so a single leaf yields at most one kind
    and overlapping kinds never both fire:

      1. ``exact``       -- string leaf identical to the target.
      2. ``path-prefix`` -- string leaf differing only by a leading ``/``.
      3. ``numeric``     -- leaf and target are the same number (this is where a
         numeric leaf such as ``748`` matches the string ``"748"``, and where
         ``"748.0"`` matches ``"748"``).
      4. ``substring``   -- string leaf that contains the target.

    Pure -- no IO.
    """
    if isinstance(leaf, str):
        if leaf == value:
            return "exact"
        if _is_path_prefix(leaf, value):
            return "path-prefix"
        if _numeric_equal(leaf, value):
            return "numeric"
        if value in leaf:
            return "substring"
        return None
    # numeric (or other scalar) leaf: only a numeric equivalence can match a
    # string target (string/number cross-type equality -> "numeric", Req 2.3).
    if _numeric_equal(leaf, value):
        return "numeric"
    return None


def _walk_for_value(
    obj: object,
    value: str,
    path: str,
    depth: int,
    out: List[PathMatch],
) -> None:
    """Recursively collect :class:`PathMatch` for ``value`` under ``obj``,
    building dotted/indexed paths. Bounded by ``_MAX_JSON_DEPTH``. Pure."""
    if depth > _MAX_JSON_DEPTH:
        return
    if isinstance(obj, dict):
        for key, child in obj.items():
            child_path = f"{path}.{key}" if path else f"{key}"
            _walk_for_value(child, value, child_path, depth + 1, out)
    elif isinstance(obj, list):
        for i, child in enumerate(obj):
            _walk_for_value(child, value, f"{path}[{i}]", depth + 1, out)
    else:
        kind = _classify_leaf(obj, value)
        if kind is not None:
            out.append(PathMatch(path=path, leaf=obj, kind=kind))


def json_paths_for_value(obj: object, value: str) -> List[PathMatch]:
    """Return every JSON path whose leaf matches ``value`` (Req 2.1-2.4, 2.6).

    Walks dicts and lists to a bounded depth (``_MAX_JSON_DEPTH``) and never
    raises on cyclic-free arbitrary JSON. Each returned :class:`PathMatch` has a
    ``path`` that resolves back to its ``leaf`` via :func:`get_by_path`
    (Property 1, locator soundness). Every leaf equal to the target is reported
    (Property 2, completeness for exact matches); see :func:`_classify_leaf` for
    the match-kind precedence. The tool does not pick "the" field -- all matches
    are returned for the developer to interpret.

    Pure -- no network/browser/file IO (Req 2.6, Property 7).
    """
    out: List[PathMatch] = []
    if value == "":
        # An empty target would "substring-match" every string leaf; that is
        # noise, not a location, so report nothing (the caller treats a
        # value with no matches as unresolved, Req 2.5).
        return out
    _walk_for_value(obj, value, "", 0, out)
    return out


# Path segments look like ``key`` or ``key[0]`` or a bare ``[0]`` (list root),
# with one or more bracketed indices, e.g. ``items[0][1]``.
_PATH_SEGMENT = re.compile(r"^([^\[\]]*)((?:\[\d+\])*)$")


def get_by_path(obj: object, path: str) -> object:
    """Resolve a dotted/indexed ``path`` (as produced by
    :func:`json_paths_for_value`) against ``obj`` and return the leaf value.

    ``""`` denotes the root object itself. Dict keys are dot-separated and list
    indices are bracketed, e.g. ``data.items[0].name``. This is the inverse of
    the locator and is shared with the Phase 3 scaffold's runtime path access.
    Raises ``KeyError``/``IndexError``/``TypeError`` for a path that does not
    resolve. Pure -- no IO.

    Note: keys containing ``.`` or ``[`` are not representable in this notation;
    the captured manga JSON uses identifier-like keys, for which it round-trips.
    """
    if path == "":
        return obj
    cur: object = obj
    for segment in path.split("."):
        match = _PATH_SEGMENT.match(segment)
        if match is None:
            raise KeyError(f"unparseable path segment: {segment!r}")
        key, indices = match.group(1), match.group(2)
        if key != "":
            cur = cur[key]  # type: ignore[index]
        for idx in re.findall(r"\[(\d+)\]", indices):
            cur = cur[int(idx)]  # type: ignore[index]
    return cur


# ---------------------------------------------------------------------------
# Phase 2 -- sibling-mismatch (gotcha) check: once a value is located, look at
# the fields *next to* it and warn when one looks like it should carry the same
# number but disagrees -- e.g. a ``chapter_number`` that is a sequence counter,
# not the displayed chapter (refactoring-plan "Problem A"). Hints only: the tool
# never asserts the sibling is wrong, and stays silent when nothing disagrees so
# that the absence of warnings is meaningful (Req 3.4, Property 4). Pure -- no IO.
# ---------------------------------------------------------------------------

# Sibling key *names* that hint the field should carry the matched datum as a
# number (case-insensitive substring match, Req 3.2): ``chapter_number``,
# ``page_count``, ``manga_id`` all qualify. Used to specialise the hint wording.
_NUMBER_KEY_HINTS = ("number", "count")
_ID_KEY_HINTS = ("id",)

# First integer/decimal run embedded in a string, e.g. ``700.5`` inside
# ``"Chapter 700.5 : Uzumaki Naruto"`` (Req 3.2).
_EMBEDDED_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class SiblingWarning:
    """A hint that a field adjacent to a matched value disagrees with it.

    ``sibling_path`` is the full dotted/indexed path of the disagreeing sibling
    (resolvable via :func:`get_by_path`); ``sibling_value`` is its raw value;
    ``message`` is advisory prose (Req 3.3) -- the tool never asserts the
    sibling is wrong, only that it *might* be a gotcha (e.g. a sequence counter).
    """

    sibling_path: str
    sibling_value: object
    message: str


def _number_in_target(value: str) -> Optional[float]:
    """The number a target value denotes or contains, or ``None`` if it has
    none.

    A bare numeric target (``"748"``, ``"700.5"``) is parsed via
    :func:`_to_number`; otherwise the first embedded ``\\d+(\\.\\d+)?`` run is
    taken (``"Chapter 700.5 : Uzumaki Naruto"`` -> ``700.5``). Returning
    ``None`` means there is nothing numeric to compare a sibling against, so the
    checker stays silent (Req 3.4). Pure -- no IO.
    """
    bare = _to_number(value)
    if bare is not None:
        return bare
    found = _EMBEDDED_NUMBER.search(value)
    return float(found.group()) if found else None


def _format_number(n: float) -> str:
    """Render a comparison number without a spurious ``.0`` (``748.0`` ->
    ``"748"``, ``700.5`` -> ``"700.5"``). Pure."""
    return str(int(n)) if n.is_integer() else str(n)


def _sibling_hint(key: str, target_str: str, sibling_value: object) -> str:
    """Advisory message for a disagreeing sibling, specialised by key shape
    (Req 3.3). Always phrased as a hint ("likely", "might"). Pure -- no IO."""
    base = (
        f"sibling {key!r}={sibling_value!r} disagrees with {target_str} "
        f"in the matched value"
    )
    low = key.lower()
    if any(h in low for h in _NUMBER_KEY_HINTS):
        return (
            f"{base}; it is likely a sequence counter rather than the displayed "
            f"number -- derive the number from the matched field instead"
        )
    if any(h in low for h in _ID_KEY_HINTS):
        return f"{base}; it might be an internal id rather than the displayed number"
    return f"{base}; verify which field actually holds the value you want"


def sibling_mismatch_check(
    obj: object, match: PathMatch, value: str
) -> List[SiblingWarning]:
    """Flag sibling fields that look like they should hold the matched value's
    number but disagree (Req 3.1-3.4).

    Resolves the dict that *directly* contains the matched leaf (the parent of
    ``match.path``) and inspects its other keys. A sibling is flagged when the
    target contains a number AND the sibling *looks* numeric/identifier-like --
    a ``*number*``/``*count*``/``*id*`` key (case-insensitive) or any numeric
    value -- AND its numeric value differs from the target's number. Comparison
    is numeric, so ``748`` and ``"748"`` agree (no warning). Matching siblings
    never warn, so the absence of warnings is meaningful (Req 3.4, Property 4);
    warnings are phrased as hints, not assertions (Req 3.3).

    Returns ``[]`` when the matched leaf is not inside a dict (a list element or
    the root -- no named siblings, Req 3.1), when the target contains no number,
    or when no sibling disagrees. Pure -- no network/browser/file IO
    (Property 7).
    """
    # The matched leaf has named siblings only if it sits in a dict. ``""`` is
    # the root, and a path ending in an index (``...[0]``) points at a list
    # element -- neither exposes named siblings (Req 3.1).
    if match.path == "":
        return []
    last_segment = match.path.split(".")[-1]
    if last_segment.endswith("]"):
        return []
    matched_key = last_segment
    parent_path = match.path.rsplit(".", 1)[0] if "." in match.path else ""

    try:
        parent = get_by_path(obj, parent_path)
    except (KeyError, IndexError, TypeError):
        return []
    if not isinstance(parent, dict):
        return []

    target_number = _number_in_target(value)
    if target_number is None:
        # No number in the target -> nothing for a numeric sibling to disagree
        # with -> stay silent (Req 3.4, no noise).
        return []
    target_str = _format_number(target_number)

    warnings: List[SiblingWarning] = []
    for key, sibling_value in parent.items():
        if key == matched_key:
            continue
        sibling_number = _to_number(sibling_value)
        if sibling_number is None:
            # Non-numeric sibling: nothing to compare numerically. A key whose
            # *name* hints a number (e.g. an id) but whose value is a
            # non-numeric string (e.g. "WYXlbzbY") gives no disagreement to
            # surface -> skip (conservative, Req 3.4).
            continue
        if sibling_number == target_number:
            continue  # agrees -> never warn (Req 3.4, Property 4)
        sibling_path = f"{parent_path}.{key}" if parent_path else key
        warnings.append(
            SiblingWarning(
                sibling_path=sibling_path,
                sibling_value=sibling_value,
                message=_sibling_hint(key, target_str, sibling_value),
            )
        )
    return warnings


# ---------------------------------------------------------------------------
# Phase 2 -- field map: tie the locator and the gotcha checker together across
# every captured JSON body. Given the developer's known values
# (``name=value`` examples), report, per value, the path(s) it lives at, which
# capture each came from, and any sibling-mismatch hints -- plus an explicit
# "unresolved" marker for values found nowhere (Req 4.2-4.4). The tool lists
# *all* matches and never picks "the" field; the developer interprets
# (Property 6, advisory-only). Pure -- the CLI wrapper does the file IO and the
# ``__NEXT_DATA__`` extraction (Property 7); this works uniformly on any parsed
# JSON, whether it came from an ``api_*.json`` body or embedded page data.
# ---------------------------------------------------------------------------


@dataclass
class FieldMapEntry:
    """One developer-supplied example mapped across all captured JSON.

    ``name`` is the label the developer gave (e.g. ``"title"``); ``value`` is
    the value they can see on the page. ``matches`` is every
    ``(source_file, PathMatch)`` the value was located at, across *all*
    captures -- the tool does not pick a single field (Req 4.4, Property 6).
    ``warnings`` collects the sibling-mismatch hints for those matches
    (de-duplicated). An entry with an empty ``matches`` list is *unresolved*
    (the value was found nowhere) and is reported as such rather than dropped
    (Req 4.3, Property 3 -- no silent loss).
    """

    name: str
    value: str
    matches: List[Tuple[str, PathMatch]]
    warnings: List[SiblingWarning]


def build_field_map(
    captures: Dict[str, object],
    examples: Dict[str, str],
) -> List[FieldMapEntry]:
    """Locate each example value across every parsed JSON capture (Req 4.2-4.4).

    ``captures`` maps a source-file label (e.g. ``"api_01.json"`` or
    ``"page.html#__NEXT_DATA__"``) to an already-parsed JSON object;
    ``examples`` maps a developer label to the value they can see on the page.

    For each ``(name, value)`` -- in the input order of ``examples`` -- the
    locator (:func:`json_paths_for_value`) runs over every capture and *all*
    matches are collected as ``(source_file, PathMatch)`` tuples; the tool does
    not pick "the" field (Req 4.4). Each match is also run through
    :func:`sibling_mismatch_check` against the capture it came from, and the
    resulting :class:`SiblingWarning` hints are gathered (de-duplicated by
    ``(sibling_path, message)`` so a hint recurring across captures is reported
    once). Every example produces exactly one :class:`FieldMapEntry`, even when
    nothing matched -- an empty ``matches`` list is how "unresolved" is
    represented, so no input is silently lost (Req 4.3, Property 3).

    Pure -- no network/browser/file IO. It reads only its parsed inputs, so it
    treats standalone ``api_*.json`` bodies and embedded ``__NEXT_DATA__``
    payloads identically (the CLI wrapper supplies both, Property 7).
    """
    entries: List[FieldMapEntry] = []
    for name, value in examples.items():
        matches: List[Tuple[str, PathMatch]] = []
        warnings: List[SiblingWarning] = []
        seen_warnings: set = set()
        for source_file, capture in captures.items():
            for match in json_paths_for_value(capture, value):
                matches.append((source_file, match))
                for warning in sibling_mismatch_check(capture, match, value):
                    key = (warning.sibling_path, warning.message)
                    if key in seen_warnings:
                        continue
                    seen_warnings.add(key)
                    warnings.append(warning)
        entries.append(
            FieldMapEntry(name=name, value=value, matches=matches, warnings=warnings)
        )
    return entries


def render_field_map(entries: List[FieldMapEntry]) -> str:
    """Render a readable, advisory field-map report (Req 4.3, 4.4; Property 6).

    Resolved examples come first: per entry, the label and value, then every
    matching path with its source file and match kind, then any sibling-mismatch
    hint lines. When several paths match one value they are *all* listed -- the
    report never asserts a single answer (Req 4.4, Property 6). Unresolved
    examples (no matches) are gathered into a dedicated trailing section so they
    are visible, not dropped (Req 4.3, Property 3). Pure -- no IO.
    """
    resolved = [e for e in entries if e.matches]
    unresolved = [e for e in entries if not e.matches]

    lines = [
        "# Field map (advisory -- all matches listed; you pick the field)\n",
    ]

    if not resolved and not unresolved:
        lines.append("(no values supplied)")
        return "\n".join(lines) + "\n"

    for entry in resolved:
        lines.append(
            f"{entry.name} = {entry.value!r}  ({len(entry.matches)} match(es))"
        )
        for source_file, match in entry.matches:
            lines.append(f"  [{match.kind}] {source_file}: {match.path}")
            lines.append(f"      leaf={match.leaf!r}")
        for warning in entry.warnings:
            lines.append(f"  !! hint: {warning.message}")
            lines.append(f"     (sibling {warning.sibling_path})")
        lines.append("")

    if unresolved:
        lines.append("# Unresolved (found in no capture -- check the exact value)")
        for entry in unresolved:
            lines.append(f"  {entry.name} = {entry.value!r}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def parse_examples(tokens: List[str]) -> Dict[str, str]:
    """Parse ``name=value`` CLI tokens into an ``{name: value}`` map (Req 4.1).

    Splits each token on the *first* ``=`` so a value may itself contain ``=``
    (e.g. ``url=/x?a=b`` -> ``{"url": "/x?a=b"}``). Insertion order is preserved
    so the field-map report lists examples in the order the developer gave them.
    A token with no ``=`` cannot name a value, so it raises ``ValueError`` (the
    CLI turns this into a clean ``argparse`` error). Pure -- no IO.
    """
    examples: Dict[str, str] = {}
    for token in tokens:
        name, sep, value = token.partition("=")
        if sep == "":
            raise ValueError(
                f"invalid --map-by-example token {token!r}: expected name=value"
            )
        examples[name] = value
    return examples


def _write(out_dir: Path, name: str, text: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(text, encoding="utf-8", errors="replace")
    print(f"  wrote {out_dir / name}  ({len(text)} chars)")


def load_captures(out_dir: Path) -> Dict[str, object]:
    """Load already-captured probe artifacts in ``out_dir`` into the
    ``{source_file: parsed_json}`` map :func:`build_field_map` expects.

    Reads only what the probe already wrote -- it never triggers a new
    browser/network capture (Req 7.1):

      * every ``api_*.json`` body in the directory is parsed and keyed by its
        filename; a file whose contents don't parse as JSON is skipped, not
        raised on (design "Malformed JSON in a capture -> skipped").
      * if ``page.html`` is present, its embedded ``__NEXT_DATA__`` payload is
        extracted via :func:`_next_data_from_html` and, when found, added under
        ``"page.html#__NEXT_DATA__"`` (Req 4.2 -- embedded JSON is searched too).

    A missing directory or absent files yield an empty map rather than an error
    (design: missing files treated as empty). The only IO is reading; the
    returned data is what makes the field-map step unit-testable (Property 7).
    """
    captures: Dict[str, object] = {}
    if not out_dir.is_dir():
        return captures

    for path in sorted(out_dir.glob("api_*.json")):
        try:
            captures[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            # malformed JSON (or unreadable) capture -> skip gracefully
            continue

    page_html = out_dir / "page.html"
    if page_html.is_file():
        try:
            next_data = _next_data_from_html(page_html.read_text(encoding="utf-8"))
        except OSError:
            next_data = None
        if next_data is not None:
            captures["page.html#__NEXT_DATA__"] = next_data

    return captures


def write_field_map(out_dir: Path, examples: Dict[str, str]) -> Path:
    """Build a field map over the existing captures in ``out_dir`` and write
    ``field_map.txt`` (Req 4.2, 4.3). Thin IO wrapper around the pure analysis.

    Operates purely on artifacts already in ``out_dir`` -- it performs no
    network/browser capture (Req 7.1, Property 7). Returns the path written.
    """
    captures = load_captures(out_dir)
    entries = build_field_map(captures, examples)
    _write(out_dir, "field_map.txt", render_field_map(entries))
    return out_dir / "field_map.txt"


def _dump_api_bodies(
    out_dir: Path,
    api_bodies: List[Tuple[str, str]],
    api_misses: Optional[List[str]] = None,
) -> None:
    """Write each captured API response body to its own file, plus an index.

    JSON bodies are pretty-printed for readability; anything that doesn't parse
    is written verbatim. The index maps each dump file back to its source URL so
    you can see at a glance which endpoint served what (e.g. spot the search API
    among the home-page boot calls). ``api_misses`` lists endpoints we saw but
    couldn't read a body for -- recorded so they aren't hidden.
    """
    import json as _json

    misses = list(dict.fromkeys(api_misses or []))

    if not api_bodies:
        lines = ["(no API/JSON response bodies captured)"]
        if misses:
            lines.append("")
            lines.append("# saw these data endpoints but could not read a body:")
            lines.extend(misses)
        _write(out_dir, "api_index.txt", "\n".join(lines) + "\n")
        return

    index_lines = [f"# {len(api_bodies)} API/JSON response(s) captured\n"]
    for i, (url, body) in enumerate(api_bodies, start=1):
        fname = api_dump_filename(i, url)
        try:
            pretty = _json.dumps(_json.loads(body), indent=2, ensure_ascii=False)
        except Exception:
            pretty = body  # not JSON (or truncated) -- keep raw
        _write(out_dir, fname, pretty)
        index_lines.append(f"{fname}\t<- {url}  ({len(body)} chars)")
    if misses:
        index_lines.append("")
        index_lines.append("# saw these data endpoints but could not read a body:")
        index_lines.extend(misses)
    _write(out_dir, "api_index.txt", "\n".join(index_lines) + "\n")


async def _read_cookies(tab, cdp) -> Dict[str, str]:  # pragma: no cover - browser
    """Read the current page's cookies via CDP (best effort -> {} on failure).

    These are the session cookies a cleared-Cloudflare browser holds; passing
    them to the image ladder lets curl_cffi/cloudscraper be judged with the same
    session the parser would reuse (the MangaFire 'harvest cookies' pattern)."""
    try:
        raw = await tab.send(cdp.network.get_cookies())
        return {c.name: c.value for c in raw}
    except Exception:
        return {}


async def _refetch_misses(tab, misses, api_bodies) -> int:
    """Re-fetch endpoints we couldn't read from the CDP buffer, from inside the
    page (credentialed, same-origin). Appends recovered (url, body) pairs to
    ``api_bodies`` and returns how many were recovered. Best effort."""
    import json as _json

    already = {u for u, _ in api_bodies}
    recovered = 0
    for url in dict.fromkeys(misses):
        if url in already:
            continue
        js = (
            "(async () => {"
            f"  try {{ const r = await fetch({_json.dumps(url)}, "
            "      {credentials: 'include', "
            "       headers: {'X-Requested-With': 'XMLHttpRequest'}});"
            "    return await r.text(); } catch (e) { return null; }"
            "})()"
        )
        try:
            body = await tab.evaluate(js, await_promise=True)
        except Exception as err:  # pragma: no cover - browser timing
            logger.debug(f"in-page refetch failed for {url}: {err}")
            continue
        if isinstance(body, str) and body:
            api_bodies.append((url, body))
            recovered += 1
    return recovered


def _plain_requests_get(url: str):
    """GET ``url`` with plain requests. Returns (html, status, error)."""
    import requests  # type: ignore

    try:
        resp = requests.get(url, timeout=20)
        return resp.text, resp.status_code, None
    except Exception as err:  # pragma: no cover - network/dns failures
        return None, None, str(err)


def looks_like_json(text: Optional[str]) -> bool:
    """True if ``text`` plausibly parses as a JSON object/array. Pure helper,
    unit-tested -- used to decide whether a backend got the real API payload
    rather than a Cloudflare HTML interstitial."""
    if not text:
        return False
    import json as _json

    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        _json.loads(stripped)
        return True
    except Exception:
        return False


def summarize_backend_probe(label: str, status, error, body) -> str:
    """Render one line describing how a backend fared against an API URL. Pure
    and unit-tested. ``status``/``error``/``body`` are what the backend returned
    (status None + error set means it raised; body is the response text)."""
    if error:
        return f"  {label}: ERROR {error}"
    is_json = looks_like_json(body)
    blen = len(body) if body else 0
    verdict = "JSON OK" if (status == 200 and is_json) else "no JSON (blocked?)"
    return f"  {label}: status={status} len={blen} json={is_json} -> {verdict}"


def backend_verdict(requests_ok_json: bool, curl_ok_json: bool) -> str:
    """Rank one endpoint's two backend probes into a single verdict token.

    ``"requests"`` when plain requests returned JSON (cheapest), else
    ``"curl_cffi"`` when curl_cffi (Chrome impersonation) returned JSON, else
    ``"blocked"`` when neither did. The ranking requests < curl_cffi mirrors
    :func:`_pick_default_fetcher`; the resulting verdict is exactly what the
    in-memory ``api_backends`` map fed to :func:`synthesize_recommendation`
    expects (Req 1.2, 1.3). Pure -- no IO, derived from the SAME probe attempts
    :func:`_check_api_backends` already made (no extra network calls, Req 7.1).
    """
    if requests_ok_json:
        return "requests"
    if curl_ok_json:
        return "curl_cffi"
    return "blocked"


def _check_api_backends(
    api_urls: List[str], max_urls: int = 3
) -> Tuple[str, Dict[str, str]]:
    """For a few captured API URLs, try plain requests and curl_cffi (Chrome
    impersonation) and report whether each returns real JSON without a browser.

    This answers the key parser question: can the site's API be hit with a
    cheap HTTP client (curl_cffi/requests), or does it need a full browser?
    Only the data-looking endpoints are worth checking, so the caller passes a
    filtered list; we cap how many we hit to stay polite.

    Returns BOTH the rendered ``api_backends.txt`` text AND an in-memory
    ``{endpoint_url: verdict}`` map (verdict per :func:`backend_verdict`) so the
    caller can feed the map straight into :func:`synthesize_recommendation`
    without a second probing pass -- the verdict is derived from the SAME
    requests/curl_cffi attempts rendered into the text (no extra site hammering,
    Req 7.1).
    """
    import requests  # type: ignore

    # only real data endpoints, de-duped, capped
    candidates = [u for u in dict.fromkeys(api_urls) if is_api_like_url(u)][:max_urls]
    if not candidates:
        return "(no API-looking endpoints to check)\n", {}

    lines = [
        "# Can the API be reached WITHOUT a browser?",
        "# (tries plain requests + curl_cffi Chrome impersonation per endpoint)",
        "",
    ]
    verdict_map: Dict[str, str] = {}
    for url in candidates:
        lines.append(url)
        # plain requests
        req_status: Optional[int] = None
        req_body: Optional[str] = None
        req_error: Optional[str] = None
        try:
            resp = requests.get(url, timeout=20)
            req_status, req_body = resp.status_code, resp.text
        except Exception as err:
            req_error = str(err)
        lines.append(
            summarize_backend_probe("requests   ", req_status, req_error, req_body)
        )
        # curl_cffi with Chrome impersonation
        curl_status: Optional[int] = None
        curl_body: Optional[str] = None
        curl_error: Optional[str] = None
        try:
            from curl_cffi import requests as creq  # type: ignore

            cresp = creq.Session(impersonate="chrome").get(url, timeout=20)
            curl_status, curl_body = cresp.status_code, cresp.text
        except Exception as err:
            curl_error = str(err)
        lines.append(
            summarize_backend_probe("curl_cffi  ", curl_status, curl_error, curl_body)
        )
        lines.append("")
        # Reuse the SAME outcomes (status + body) to rank the verdict -- "got
        # JSON" matches summarize_backend_probe's "JSON OK": status 200, no
        # error, and a body that parses as JSON.
        req_ok_json = (
            req_error is None and req_status == 200 and looks_like_json(req_body)
        )
        curl_ok_json = (
            curl_error is None and curl_status == 200 and looks_like_json(curl_body)
        )
        verdict_map[url] = backend_verdict(req_ok_json, curl_ok_json)
    lines.append(
        "If curl_cffi shows 'JSON OK', the parser can use CurlCffiFetcher (no "
        "browser) for these endpoints. If both are blocked but the browser "
        "captured a body, the parser needs BrowserFetcher."
    )
    return "\n".join(lines) + "\n", verdict_map


# ---------------------------------------------------------------------------
# C10 -- uniform "cheapest working fetcher" ladder. The probe should not just
# find WHERE the content is but the LEAST-INVOLVED way to GET it (a browser per
# chapter is a drag). The decision (which tier wins) and the candidate-url
# extraction are pure + fixture-tested; the per-tier network GET is injected so
# tests never touch the network and the live attempt uses scraper.fetchers.
# ---------------------------------------------------------------------------

# cheapest -> most invasive
FETCHER_LADDER = ("requests", "curl_cffi", "cloudscraper", "browser")


def cheapest_working(outcomes: Dict[str, bool]) -> Optional[str]:
    """The cheapest fetcher tier that worked, per the ladder order, or None."""
    for name in FETCHER_LADDER:
        if outcomes.get(name):
            return name
    return None


def candidate_image_urls(html: str, limit: int = 5) -> List[str]:
    """Page-image urls from the largest ``<img>`` cluster (the reader
    container), preferring ``data-src`` (lazy) then ``src``, skipping ``data:``
    placeholders. The largest cluster is the page container, so nav/logo images
    (elsewhere in the DOM) are excluded. Handles both src and data-src readers.
    Pure / unit-tested against captured reader fixtures.
    """
    soup = BeautifulSoup(html, "lxml")
    parent_counts: Counter = Counter()
    by_parent: Dict[int, list] = {}
    for img in soup.find_all("img"):
        parent = img.parent
        if parent is None:
            continue
        parent_counts[id(parent)] += 1
        by_parent.setdefault(id(parent), []).append(img)
    if not parent_counts:
        return []
    best_id, _ = parent_counts.most_common(1)[0]
    urls: List[str] = []
    for img in by_parent[best_id]:
        url = attr(img, "data-src") or attr(img, "src")
        if url and not url.startswith("data:"):
            urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _real_image_attempt(
    name: str, url: str, headers: Dict[str, str], cookies: Dict[str, str]
) -> Tuple[bool, str]:  # pragma: no cover - real network
    """GET ``url`` with one fetcher tier and report whether it returned a real
    image (status 200 + an ``image/*`` content type). curl_cffi/cloudscraper
    carry the session ``cookies``; ``requests`` is the bare baseline."""
    try:
        status: int
        ctype: str
        if name == "requests":
            import requests  # type: ignore

            r = requests.get(url, headers=headers, timeout=20)
            status, ctype = r.status_code, str(r.headers.get("content-type", "?"))
        elif name == "curl_cffi":
            from curl_cffi import requests as creq  # type: ignore

            session = creq.Session(impersonate="chrome")
            if cookies:
                session.cookies.update(cookies)
            cr = session.get(url, headers=headers, timeout=20)
            status, ctype = cr.status_code, str(cr.headers.get("content-type", "?"))
        elif name == "cloudscraper":
            import cloudscraper  # type: ignore

            scraper = cloudscraper.create_scraper()
            cs = scraper.get(url, headers=headers, cookies=cookies or None, timeout=20)
            status, ctype = cs.status_code, str(cs.headers.get("content-type", "?"))
        else:
            return False, "not attempted"
        ok = status == 200 and ctype.startswith("image")
        return ok, f"status={status} content-type={ctype}"
    except Exception as err:
        return False, f"ERROR {err}"


# image tier subset: a browser rarely fetches the image bytes itself
_IMAGE_LADDER = ("requests", "curl_cffi", "cloudscraper")


def check_image_ladder(
    urls: List[str],
    referer: str,
    cookies: Optional[Dict[str, str]] = None,
    attempt: Optional[Callable[..., Tuple[bool, str]]] = None,
) -> Tuple[Optional[str], str]:
    """Run the image fetcher ladder over a few candidate image urls and report
    the CHEAPEST tier that returns a real image.

    Tries requests -> curl_cffi -> cloudscraper (browser rarely fetches the
    image bytes), carrying the page ``Referer`` and -- for the
    fingerprint/cookie-aware tiers -- the captured session ``cookies`` (the
    MangaFire pattern: clear once, reuse cookies, download cheap). A tier counts
    as working if ANY candidate url returns a real image. Returns
    ``(cheapest_working_fetcher, report_text)``; ``cheapest_working_fetcher`` is
    None when no tier got an image. The per-tier GET is injected via ``attempt``
    (defaults to the real backends; mocked in tests). Decision logic is pure.
    """
    attempt = attempt or _real_image_attempt
    cookies = cookies or {}
    test_urls = [u for u in urls if u][:3]
    lines = ["# Image fetchability ladder (cheapest tier that returns an image)\n"]
    if not test_urls:
        lines.append("(no candidate image urls found on the page)")
        return None, "\n".join(lines) + "\n"
    lines.append(f"Referer: {referer}")
    lines.append(f"session cookies carried: {'yes' if cookies else 'no'}")
    lines.append(f"candidate images ({len(test_urls)}):")
    for u in test_urls:
        lines.append(f"  {u}")
    lines.append("")

    headers = {"Referer": referer} if referer else {}
    outcomes: Dict[str, bool] = {}
    for name in _IMAGE_LADDER:
        tier_ok = False
        details: List[str] = []
        for u in test_urls:
            ok, detail = attempt(name, u, headers, cookies)
            details.append(detail)
            if ok:
                tier_ok = True
                break
        outcomes[name] = tier_ok
        lines.append(
            f"{name:11} -> {'OK (real image)' if tier_ok else 'no'}  ({details[-1]})"
        )

    cheapest = cheapest_working(outcomes)
    lines.append("")
    if cheapest:
        lines.append(f"-> cheapest working image fetcher: {cheapest}")
    else:
        lines.append(
            "-> NO cheap tier returned an image; the CDN likely needs the live "
            "browser session's cookies (see C11) or a Referer/token we don't have."
        )
    return cheapest, "\n".join(lines) + "\n"


def is_api_like_url(url: str) -> bool:
    """True if ``url`` looks like a JSON/AJAX API endpoint (by path shape).

    Pure and unit-tested. Mirrors the request-capture filter so the
    request-URL log and the response-body capture agree on what counts.
    """
    u = url.split("?", 1)[0].lower()
    return "ajax" in u or "/api/" in u or u.endswith(".json")


def is_json_mime(mime: Optional[str]) -> bool:
    """True if a response mime-type denotes JSON. Pure and unit-tested."""
    if not mime:
        return False
    m = mime.lower()
    return "json" in m  # application/json, text/json, *+json


def api_dump_filename(index: int, url: str) -> str:
    """Build a stable, filesystem-safe filename for a captured API body.

    e.g. (3, "https://mangak.io/api/search?q=naruto") -> "api_03_search.json".
    Pure and unit-tested.
    """
    path = urlparse(url).path
    last = path.rstrip("/").split("/")[-1] if path else ""
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", last) or "response"
    if not slug.endswith(".json"):
        slug += ".json"
    return f"api_{index:02d}_{slug}"


# CSS selectors we try, in order, to locate a site's search input.
_SEARCH_INPUT_SELECTORS = (
    "input[type=search]",
    "input[name=keyword]",
    "input[name=q]",
    "input[name=search]",
    ".search-inner input",
    "header input[type=text]",
    "input[type=text]",
)


async def _drive_search(
    tab,
    cdp,
    query: str,
    wait: float,
    home_html: str,
    home_report,
    ajax_urls: List[str],
    out_dir: Path,
) -> None:
    """Type ``query`` into the site's search box and submit it *for real*.

    The earlier version only set ``input.value`` and called
    ``form.requestSubmit()``, which silently did nothing on sites that navigate
    on Enter or run JS-driven (debounced) search -- the captured "search page"
    came back identical to the home page. Here we focus a real input, send real
    keystrokes, press a real Enter, and fall back to clicking a submit control.
    Crucially we then *verify* something actually changed (URL navigated, ajax
    fired, or the HTML differs) and report honestly when it did not.
    """
    print(f"[probe] typing search query: {query!r}")

    # 1. locate a search input via the candidate selectors
    box = None
    used_selector = None
    for selector in _SEARCH_INPUT_SELECTORS:
        try:
            box = await tab.select(selector, timeout=2)
        except Exception:
            box = None
        if box:
            used_selector = selector
            break

    if not box:
        print("[probe] search box: NOT FOUND (no input matched known selectors)")
        _write(
            out_dir,
            "search_page.html",
            "(no search input found; selectors tried:\n"
            + "\n".join(_SEARCH_INPUT_SELECTORS)
            + ")\n",
        )
        return

    print(f"[probe] search box: found via {used_selector!r}")

    url_before = await tab.evaluate("location.href")
    ajax_before = len(ajax_urls)

    # 2. type real keystrokes (focus + per-char char events)
    try:
        await box.clear_input()
    except Exception:
        pass
    await box.send_keys(query)
    # Let any debounced live-search XHR fire and complete before we capture.
    # (Many SPA search boxes query as you type, with no navigation at all.)
    await tab.wait(3)

    # 3. press a real Enter key (keyDown + keyUp with the proper key codes)
    for kind in ("keyDown", "keyUp"):
        await tab.send(
            cdp.input_.dispatch_key_event(
                type_=kind,
                key="Enter",
                code="Enter",
                windows_virtual_key_code=13,
                native_virtual_key_code=13,
            )
        )
    await tab.wait(wait)

    # 4. if nothing moved, fall back to clicking a submit-looking control
    url_after = await tab.evaluate("location.href")
    if url_after == url_before and len(ajax_urls) == ajax_before:
        clicked = await tab.evaluate(
            "(() => {"
            "  const b = document.querySelector("
            "    'button[type=submit], .search-inner button, "
            "     form[action*=search] button, button[class*=search]');"
            "  if (b) { b.click(); return true; } return false;"
            "})()"
        )
        if clicked is True:
            print("[probe] Enter did nothing; clicked a submit control instead")
            await tab.wait(wait)
            url_after = await tab.evaluate("location.href")

    # 5. capture and analyze the result
    search_html = await tab.get_content()
    _write(out_dir, "search_page.html", search_html)
    search_report = analyze_html(search_html)
    _write(out_dir, "search_candidates.txt", search_report.render())

    # 6. report honestly whether the search action actually did anything
    navigated = url_after != url_before
    ajax_fired = len(ajax_urls) > ajax_before
    html_changed = search_html != home_html
    if navigated:
        print(f"[probe] search navigated to {url_after}")
    elif ajax_fired:
        print("[probe] search fired ajax (likely API-backed in-page results)")
    elif html_changed:
        print("[probe] search did not navigate, but the DOM changed in place")
    else:
        print(
            "[probe] !! search produced NO change (URL, ajax, and HTML are all "
            "identical to the home page) -- the search box was found but the "
            "query never took effect. The site may need a different submit "
            "mechanism, or be blocking automated input."
        )

    if search_report.looks_like_challenge and not home_report.looks_like_challenge:
        print(
            "[probe] !! challenge appeared AFTER the search action "
            "(the search request is what triggers it)"
        )


async def _probe(
    url: str,
    out_dir: Path,
    wait: float = 8.0,
    search: Optional[str] = None,
    find: Optional[str] = None,
) -> None:
    """Drive a browser, capture ajax URLs + rendered HTML, write the report.

    If ``search`` is given, after the page loads we type the query into a
    search box and capture again -- so you can see whether the *search action*
    (rather than the initial load) is what trips a Cloudflare challenge. The
    report flags challenge markers in the captured HTML either way.
    """
    import nodriver as nd
    from nodriver import cdp

    browser = await nd.start(headless=False, sandbox=False, no_sandbox=True)
    try:
        ajax_urls: List[str] = []
        # Captured XHR/Fetch responses. We track ALL of them by resource type
        # (not URL guesswork), because a live-search endpoint often has a plain
        # path (e.g. /search?q=...) with no /api/ or .json marker. request_id ->
        # (url, mime). Bodies pulled on LoadingFinished; misses are recorded so
        # the index shows what we saw-but-couldn't-read rather than hiding it.
        data_targets: dict = {}
        api_bodies: List[Tuple[str, str]] = []
        api_misses: List[str] = []
        tab = await browser.get("about:blank")

        async def on_request(evt: cdp.network.RequestWillBeSent):
            u = evt.request.url
            if is_api_like_url(u):
                ajax_urls.append(u)

        async def on_response(evt: cdp.network.ResponseReceived):
            # Mark XHR/Fetch responses (the ones a JS app uses for data), plus
            # anything with a JSON mime-type regardless of resource type.
            rtype = getattr(evt.type_, "value", str(evt.type_))
            url_ = evt.response.url
            is_data = rtype in ("XHR", "Fetch") or is_json_mime(evt.response.mime_type)
            if is_data:
                data_targets[evt.request_id] = (url_, evt.response.mime_type)
                if url_ not in ajax_urls:
                    ajax_urls.append(url_)

        async def on_loading_finished(evt: cdp.network.LoadingFinished):
            # Body buffer is ready now; pull it for any marked request. Best
            # effort -- a redirect/SPA cache can evict the buffer, so record a
            # miss rather than dropping the endpoint silently.
            target = data_targets.pop(evt.request_id, None)
            if target is None:
                return
            target_url, _mime = target
            try:
                body, _b64 = await tab.send(
                    cdp.network.get_response_body(evt.request_id)
                )
            except Exception as err:  # pragma: no cover - browser timing
                logger.debug(f"could not read body for {target_url}: {err}")
                api_misses.append(target_url)
                return
            if body:
                api_bodies.append((target_url, body))
            else:
                api_misses.append(target_url)

        tab.add_handler(cdp.network.RequestWillBeSent, on_request)
        tab.add_handler(cdp.network.ResponseReceived, on_response)
        tab.add_handler(cdp.network.LoadingFinished, on_loading_finished)
        await tab.send(cdp.network.enable())

        print(f"[probe] opening {url}")
        await tab.get(url)
        await tab.wait(wait)

        html = await tab.get_content()
        _write(out_dir, "page.html", html)
        report = analyze_html(html)
        if report.looks_like_challenge:
            print("[probe] !! initial load looks like a Cloudflare challenge")

        # --find: locate a user-supplied string (a chapter number, title, slug
        # you can see on the page) and report its element + container selector.
        if find:
            found = find_text(html, find)
            _write(out_dir, "find.txt", render_matches(find, found))
            print(f"[probe] --find {find!r}: {len(found)} match(es) -> find.txt")

        # Plain-requests fetch of the same URL, to decide whether this page needs
        # a browser at all (vs fetch_soup with RequestsFetcher).
        req_html, req_status, req_err = _plain_requests_get(url)
        cmp = compare_fetches(req_html, req_status, html, requests_error=req_err)
        _write(out_dir, "fetch_recommendation.txt", cmp.render(url))
        print(f"[probe] fetcher recommendation: {cmp.recommend()}")

        # Chapter stage: if the page has page-images, check whether the image CDN
        # itself is protected (plain requests vs browser cookies + Referer).
        # Image stage (C10): find the CHEAPEST fetcher that can actually pull
        # the page images -- run the ladder (requests -> curl_cffi ->
        # cloudscraper) over the candidate image urls, carrying the page Referer
        # and the live session cookies (so a hotlink/cookie-gated CDN is judged
        # fairly). Handles src- and data-src-based readers, not just data-src[0].
        img_candidates = candidate_image_urls(html)
        if img_candidates:
            cookies = await _read_cookies(tab, cdp)
            _cheapest, img_report = check_image_ladder(img_candidates, url, cookies)
            _write(out_dir, "image_check.txt", img_report)

        if search:
            await _drive_search(
                tab, cdp, search, wait, html, report, ajax_urls, out_dir
            )

        # Give any in-flight API responses (esp. those kicked off by the search)
        # a moment to finish so their bodies get captured.
        await tab.wait(2)

        # Fallback: for endpoints we saw but couldn't read from the CDP buffer
        # (SPA caches / evicted buffers), re-fetch them from inside the page
        # (credentialed, same-origin) so we still get the JSON. De-duplicated.
        if api_misses:
            recovered = await _refetch_misses(tab, api_misses, api_bodies)
            if recovered:
                print(f"[probe] recovered {recovered} body(ies) via in-page refetch")

        _write(out_dir, "ajax_log.txt", "\n".join(ajax_urls) or "(no ajax calls seen)")
        _write(out_dir, "candidates.txt", report.render())
        _dump_api_bodies(out_dir, api_bodies, api_misses)

        # Backend reachability: can the captured API endpoints be hit without a
        # browser (plain requests / curl_cffi)? Decides the parser's fetcher.
        api_seen = [u for (u, _b) in api_bodies] + api_misses
        backends_text, verdict_map = _check_api_backends(api_seen)
        _write(out_dir, "api_backends.txt", backends_text)

        # Recommendation synthesis (Phase 1): distil the artifacts just captured
        # into ONE advisory summary so the developer reads recommendation.txt
        # first instead of cross-reading five files. Uses the in-memory verdict
        # map from the SAME backend probe above (no extra network calls).
        rec = synthesize_recommendation(ajax_urls, verdict_map, api_bodies, html)
        _write(out_dir, "recommendation.txt", rec.render())
        print(f"[probe] suggested default fetcher: {rec.default_fetcher}")
    finally:
        browser.stop()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture real manga-site artifacts.")
    ap.add_argument("url", help="page URL to probe (manga page, home, search, ...)")
    ap.add_argument(
        "--site",
        help="output subfolder name, verbatim (default: derived as "
        "<url-host>/<stage>, where stage is chapters/images/search/home inferred "
        "from the URL path -- so a site's stages don't overwrite each other)",
    )
    ap.add_argument(
        "--out",
        default="probe_out",
        help="base output dir (default: probe_out/, which is gitignored). "
        "Probe captures are exploratory scratch -- promote a curated subset to "
        "tests/test_files/<site>/ by hand once you know what to keep.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing non-empty output dir (otherwise the probe "
        "refuses, so a prior stage's capture is not silently clobbered)",
    )
    ap.add_argument(
        "--search",
        help="after loading, type this query into a search box and capture again "
        "(use to see whether the search action triggers a challenge)",
    )
    ap.add_argument(
        "--find",
        help="locate this string (a chapter number, title, slug you can SEE on "
        "the page) in the HTML and report its element + container selector",
    )
    ap.add_argument(
        "--multi",
        action="store_true",
        help="single-entry multi-stage probe (C5): from the given page run "
        "search (with --search), follow the first result to the series page "
        "(chapters), then the first chapter to the reader (images) -- each "
        "captured to its own <host>/<stage> folder. With --search the entry is a "
        "HOME url (the series url is derived from the query, no slug needed); "
        "without it the entry url is treated as the series page.",
    )
    ap.add_argument(
        "--map-by-example",
        nargs="+",
        metavar="name=value",
        help="map-by-example: give one or more values you can SEE on the page "
        "as name=value pairs and write field_map.txt locating each across the "
        "captured JSON (api_*.json + embedded __NEXT_DATA__). Operates on the "
        "EXISTING captures in the output dir -- it does NOT re-capture (no "
        "browser/network), so run a normal probe first.",
    )
    args = ap.parse_args(argv)

    # --multi (C5): single-entry multi-stage probe. Runs its own per-stage
    # captures into <host>/{search,chapters,images}, so it bypasses the
    # single-stage out_dir/guard logic below.
    if args.multi:
        host = site_name_from_url(args.url)
        out_base = Path(args.out)

        def _stage_runner(
            url: str, out_dir: Path, search: Optional[str]
        ) -> None:  # pragma: no cover - drives the real browser
            import nodriver as nd

            print(f"[probe] multi stage -> {out_dir}")
            nd.loop().run_until_complete(_probe(url, out_dir, search=search))

        stages = run_multi(args.url, args.search, out_base, host, _stage_runner)
        print(
            f"\nDone (multi). Stages captured: {', '.join(stages) or 'none'}. "
            "Read each <stage>/recommendation.txt; the result/chapter auto-picks "
            "are advisory -- confirm them against the captures."
        )
        return 0

    # Default the output dir to <host>/<stage> so the chapters/images/search runs
    # of one site land in distinct folders and don't overwrite each other's
    # summary files. --site overrides the whole subpath verbatim.
    if args.site:
        out_dir = Path(args.out) / args.site
        site = args.site
    else:
        host = site_name_from_url(args.url)
        stage = stage_from_url(args.url, searching=bool(args.search))
        site = f"{host}/{stage}"
        out_dir = Path(args.out) / host / stage
    print(f"[probe] site={site} -> {out_dir}")

    # Overwrite guard: map-by-example deliberately reads EXISTING captures, so it
    # is exempt. A fresh capture into a populated dir would clobber the previous
    # run's recommendation.txt et al., so refuse unless --force.
    if not args.map_by_example and out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            ap.error(
                f"output dir {out_dir} is not empty -- a previous capture is "
                "there. Re-run with --force to overwrite it, or pass a different "
                "--site / --out to keep both."
            )
        print(f"[probe] --force: overwriting existing capture in {out_dir}")

    # Map-by-example is a standalone, browser-free mode: it reads the artifacts
    # already in out_dir and writes field_map.txt. It returns BEFORE importing
    # nodriver below, so it never drives a browser or hits the site (Req 7.1).
    if args.map_by_example:
        try:
            examples = parse_examples(args.map_by_example)
        except ValueError as err:
            ap.error(str(err))
        path = write_field_map(out_dir, examples)
        print(
            f"\nDone. Wrote {path} -- the field map locating each value across "
            "the captured JSON (advisory: all matches are listed, you pick the "
            "field). Re-run a normal probe first if it looks empty."
        )
        return 0

    import nodriver as nd

    nd.loop().run_until_complete(
        _probe(args.url, out_dir, search=args.search, find=args.find)
    )
    print(
        f"\nDone. Read {out_dir}/recommendation.txt first -- the single advisory "
        "summary of the suggested fetcher + per-stage (search / chapters / "
        "images) mechanisms synthesized from the captures; cross-check it "
        f"against the detailed files: {out_dir}/ajax_log.txt + api_index.txt "
        "(and the api_*.json dumps), api_backends.txt (can the API be hit "
        "without a browser?) and candidates.txt (and search_*.txt if --search "
        "was used), then save the relevant captures as fixtures and write the "
        "parser against them. To map values you can see on the page onto JSON "
        "paths, re-run with --map-by-example name=value ... (writes "
        "field_map.txt)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
