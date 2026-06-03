#!/usr/bin/env python3
"""
``scraper probe <url>`` -- capture real artifacts from a manga site so a parser
can be written against actual markup/endpoints instead of guesses
(refactoring-plan §4.1 / §4.3).

It drives a real browser (nodriver, headful so Cloudflare clears) and, for the
given URL:

  * logs **every ajax URL** the page fires (this alone often hands you the
    search / chapter-list / page-list API);
  * heuristically **surfaces candidate selectors** from the rendered HTML --
    chapter-looking links (``chapter-<num>``), the largest ``<img>`` cluster,
    and elements carrying ``data-number`` / ``data-src``;
  * dumps the rendered HTML + an ``ajax_log.txt`` + a ``candidates.txt`` report
    into ``tests/test_files/<site>/`` (site name derived from the URL host).

The selector analysis (``analyze_html``) is pure and unit-tested; the browser
driving is best-effort and only runs when invoked.

Usage:
  python -m scraper.probe https://mangafire.to/manga/<slug>
  python -m scraper.probe https://mangafire.to/home --site mangafire
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from scraper.parsers._html import attr

# a chapter-looking href fragment, e.g. /read/x/en/chapter-28.22 or chapter_55
_CHAPTER_HREF = re.compile(r"chapter[-_/]([\d.]+)", re.I)


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


def detect_challenge(html: str) -> List[str]:
    """
    Return challenge markers indicating the page is a Cloudflare/bot **wall**
    (not the real content). Pure -- no network.

    Strong interstitial phrases always count. Cloudflare's always-on
    infrastructure (challenge-platform script, __cf_chl, turnstile) appears on
    normal pages too, so it only counts when the page is also small
    (``< _CHALLENGE_SIZE_HINT`` chars) -- a real wall is tiny, real content is
    large. This avoids flagging every CF-fronted site as "blocked".
    """
    low = html.lower()
    strong = [m for m in _STRONG_CHALLENGE_MARKERS if m in low]
    if strong:
        return strong
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

    def recommend(self) -> str:
        """A one-line fetcher recommendation for this page."""
        if self.requests_error or self.requests_status is None:
            return "BrowserFetcher (plain requests errored)"
        if self.requests_challenge and not self.browser_challenge:
            return "BrowserFetcher (requests hits a challenge; browser clears it)"
        if self.requests_challenge and self.browser_challenge:
            return (
                "BrowserFetcher + manual/captcha step (challenge persists even in "
                "a browser -- e.g. an interactive captcha)"
            )
        if self.requests_status != 200:
            return f"BrowserFetcher (requests returned {self.requests_status})"
        if self.dynamic:
            return "BrowserFetcher (content is JS-rendered; requests HTML is sparse)"
        return "fetch_soup / RequestsFetcher (plain requests is enough)"

    def render(self, label: str) -> str:
        lines = [f"# Fetch comparison: {label}\n"]
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
        lines.append(f"-> recommended fetcher: {self.recommend()}")
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
    report.challenge_markers = detect_challenge(html)
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

    return report


def _write(out_dir: Path, name: str, text: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(text, encoding="utf-8", errors="replace")
    print(f"  wrote {out_dir / name}  ({len(text)} chars)")


def _plain_requests_get(url: str):
    """GET ``url`` with plain requests. Returns (html, status, error)."""
    import requests  # type: ignore

    try:
        resp = requests.get(url, timeout=20)
        return resp.text, resp.status_code, None
    except Exception as err:  # pragma: no cover - network/dns failures
        return None, None, str(err)


def _check_image(img_url: str, referer: str) -> str:
    """
    Probe an image URL to see whether the CDN is protected: try plain requests
    (no headers), then plain requests with a Referer. Reports status + content
    type so you can tell if images need curl_cffi/browser cookies + Referer.
    """
    import requests  # type: ignore

    lines = [f"# Image check: {img_url}\n"]
    for label, headers in (
        ("plain", {}),
        ("with Referer", {"Referer": referer}),
    ):
        try:
            resp = requests.get(img_url, headers=headers, timeout=20)
            ctype = resp.headers.get("content-type", "?")
            ok = resp.status_code == 200 and ctype.startswith("image")
            lines.append(
                f"{label}: status={resp.status_code} content-type={ctype} "
                f"{'OK (real image)' if ok else 'NOT an image -> likely protected'}"
            )
        except Exception as err:  # pragma: no cover - network failures
            lines.append(f"{label}: ERROR {err}")
    lines.append("")
    lines.append(
        "If neither returns a real image, the CDN needs browser cookies + "
        "Referer (curl_cffi with Chrome impersonation, like the MangaFire parser)."
    )
    return "\n".join(lines) + "\n"


async def _probe(
    url: str, out_dir: Path, wait: float = 8.0, search: Optional[str] = None
) -> None:
    """Drive a browser, capture ajax URLs + rendered HTML, write the report.

    If ``search`` is given, after the page loads we type the query into a
    search box and capture again -- so you can see whether the *search action*
    (rather than the initial load) is what trips a Cloudflare challenge. The
    report flags challenge markers in the captured HTML either way.
    """
    import json as _json

    import nodriver as nd
    from nodriver import cdp

    browser = await nd.start(headless=False, sandbox=False, no_sandbox=True)
    try:
        ajax_urls: List[str] = []
        tab = await browser.get("about:blank")

        async def on_request(evt: cdp.network.RequestWillBeSent):
            u = evt.request.url
            if "ajax" in u or "/api/" in u or u.endswith(".json"):
                ajax_urls.append(u)

        tab.add_handler(cdp.network.RequestWillBeSent, on_request)
        await tab.send(cdp.network.enable())

        print(f"[probe] opening {url}")
        await tab.get(url)
        await tab.wait(wait)

        html = await tab.get_content()
        _write(out_dir, "page.html", html)
        report = analyze_html(html)
        if report.looks_like_challenge:
            print("[probe] !! initial load looks like a Cloudflare challenge")

        # Plain-requests fetch of the same URL, to decide whether this page needs
        # a browser at all (vs fetch_soup with RequestsFetcher).
        req_html, req_status, req_err = _plain_requests_get(url)
        cmp = compare_fetches(req_html, req_status, html, requests_error=req_err)
        _write(out_dir, "fetch_recommendation.txt", cmp.render(url))
        print(f"[probe] fetcher recommendation: {cmp.recommend()}")

        # Chapter stage: if the page has page-images, check whether the image CDN
        # itself is protected (plain requests vs browser cookies + Referer).
        if report.data_src_samples:
            img_url = report.data_src_samples[0]
            _write(out_dir, "image_check.txt", _check_image(img_url, url))

        if search:
            print(f"[probe] typing search query: {search!r}")
            js_type = (
                "(() => {"
                "  const i = document.querySelector("
                "    'input[type=search], input[name=keyword], input[name=q], "
                "     input[name=search], .search-inner input, input[type=text]');"
                "  if (!i) return 'NO_INPUT';"
                f"  i.value = {_json.dumps(search)};"
                "  i.dispatchEvent(new Event('input', {bubbles:true}));"
                "  i.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));"
                "  if (i.form) i.form.requestSubmit && i.form.requestSubmit();"
                "  return 'OK:' + (i.name || i.type);"
                "})()"
            )
            box = await tab.evaluate(js_type)
            print(f"[probe] search box: {box}")
            await tab.wait(wait)
            search_html = await tab.get_content()
            _write(out_dir, "search_page.html", search_html)
            search_report = analyze_html(search_html)
            _write(out_dir, "search_candidates.txt", search_report.render())
            if search_report.looks_like_challenge and not report.looks_like_challenge:
                print(
                    "[probe] !! challenge appeared AFTER the search action "
                    "(the search request is what triggers it)"
                )

        _write(out_dir, "ajax_log.txt", "\n".join(ajax_urls) or "(no ajax calls seen)")
        _write(out_dir, "candidates.txt", report.render())
    finally:
        browser.stop()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture real manga-site artifacts.")
    ap.add_argument("url", help="page URL to probe (manga page, home, search, ...)")
    ap.add_argument(
        "--site",
        help="fixtures folder name (default: derived from the URL host)",
    )
    ap.add_argument(
        "--search",
        help="after loading, type this query into a search box and capture again "
        "(use to see whether the search action triggers a challenge)",
    )
    args = ap.parse_args(argv)

    site = args.site or site_name_from_url(args.url)
    out_dir = Path("tests/test_files") / site
    print(f"[probe] site={site} -> {out_dir}")

    import nodriver as nd

    nd.loop().run_until_complete(_probe(args.url, out_dir, search=args.search))
    print(
        f"\nDone. Inspect {out_dir}/ajax_log.txt and candidates.txt "
        "(and search_*.txt if --search was used), then save the relevant "
        "captures as fixtures and write the parser against them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
