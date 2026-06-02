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

    def render(self) -> str:
        lines = ["# Candidate selectors (heuristic)\n"]
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

    seen_links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _CHAPTER_HREF.search(href) and href not in seen_links:
            seen_links.add(href)
            report.chapter_links.append(href)

    for tag in soup.find_all(attrs={"data-number": True}):
        report.data_number_samples.append(str(tag)[:120])

    for tag in soup.find_all(attrs={"data-src": True}):
        report.data_src_samples.append(tag.get("data-src"))

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


async def _probe(url: str, out_dir: Path, wait: float = 8.0) -> None:
    """Drive a browser, capture ajax URLs + rendered HTML, write the report."""
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
        _write(out_dir, "ajax_log.txt", "\n".join(ajax_urls) or "(no ajax calls seen)")
        _write(out_dir, "candidates.txt", analyze_html(html).render())
    finally:
        browser.stop()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Capture real manga-site artifacts.")
    ap.add_argument("url", help="page URL to probe (manga page, home, search, ...)")
    ap.add_argument(
        "--site",
        help="fixtures folder name (default: derived from the URL host)",
    )
    args = ap.parse_args(argv)

    site = args.site or site_name_from_url(args.url)
    out_dir = Path("tests/test_files") / site
    print(f"[probe] site={site} -> {out_dir}")

    import nodriver as nd

    nd.loop().run_until_complete(_probe(args.url, out_dir))
    print(
        f"\nDone. Inspect {out_dir}/ajax_log.txt and candidates.txt, then save the "
        "relevant captures as fixtures and write the parser against them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
