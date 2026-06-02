#!/usr/bin/env python3
"""
mangafire_probe.py - capture REAL MangaFire artifacts so the parser can be
written against actual markup instead of guesses.

It drives a browser (nodriver, headful so Cloudflare clears) and dumps, into
./probe_out/:

  manga slug mode (--slug <slug>):
    - manga_page.html          rendered manga details page
    - chapter_ajax.json        raw /ajax/manga/<id>/chapter/en response
    - chapter_ajax.txt         every ajax/* response seen while loading the page
    - ajax_log.txt             list of every ajax URL the page fired

  search mode (--query "<terms>"):
    - home.html                rendered home page
    - search_ajax.json         raw ajax/manga/search response (if captured)
    - search_results.html      rendered /filter page for the query (fallback)
    - ajax_log.txt             list of every ajax URL the page fired

Usage:
  python mangafire_probe.py --slug ad-astra-scipio-and-hanniball.lww3
  python mangafire_probe.py --query "ad astra"

Then hand the probe_out/ files back for grounded parser code.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import nodriver as nd
from nodriver import cdp

BASE = "https://mangafire.to"
OUT = Path("probe_out")


def _write(name: str, text: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(text, encoding="utf-8", errors="replace")
    print(f"  wrote probe_out/{name}  ({len(text)} chars)")


async def _fetch_in_page(tab, url: str, timeout: float = 30.0) -> str:
    js = (
        "(async () => {"
        f"  const r = await fetch({json.dumps(url)}, "
        "    {credentials: 'include', "
        "     headers: {'X-Requested-With': 'XMLHttpRequest'}});"
        "  return await r.text();"
        "})()"
    )
    return await asyncio.wait_for(tab.evaluate(js, await_promise=True), timeout=timeout)


async def probe_slug(slug: str) -> None:
    manga_id = slug.rsplit(".", 1)[-1]
    manga_page = f"{BASE}/manga/{slug}"
    chapter_ajax = f"{BASE}/ajax/manga/{manga_id}/chapter/en"

    browser = await nd.start(headless=False, sandbox=False, no_sandbox=True)
    try:
        ajax_urls: list[str] = []

        tab = await browser.get("about:blank")

        async def on_request(evt: cdp.network.RequestWillBeSent):
            if "ajax" in evt.request.url:
                ajax_urls.append(evt.request.url)

        tab.add_handler(cdp.network.RequestWillBeSent, on_request)
        await tab.send(cdp.network.enable())

        print(f"[slug] opening {manga_page}")
        await tab.get(manga_page)
        await tab.wait(8)

        html = await tab.get_content()
        _write("manga_page.html", html)
        _write("ajax_log.txt", "\n".join(ajax_urls) or "(no ajax calls seen)")

        print(f"[slug] fetching chapter ajax {chapter_ajax}")
        try:
            body = await _fetch_in_page(tab, chapter_ajax)
            _write("chapter_ajax.json", body)
        except Exception as e:
            _write("chapter_ajax.json", f"FETCH FAILED: {e}")

        # also dump any ajax/manga/<id> responses we can refetch
        seen = "\n".join(u for u in ajax_urls if f"/manga/{manga_id}" in u)
        _write("chapter_ajax.txt", seen or "(no /ajax/manga/<id>/* calls observed)")
    finally:
        browser.stop()


async def probe_query(query: str) -> None:
    browser = await nd.start(headless=False, sandbox=False, no_sandbox=True)
    try:
        ajax_urls: list[str] = []
        search_url = {"u": None}

        tab = await browser.get("about:blank")

        async def on_request(evt: cdp.network.RequestWillBeSent):
            u = evt.request.url
            if "ajax" in u:
                ajax_urls.append(u)
            if "ajax/manga/search" in u and search_url["u"] is None:
                search_url["u"] = u

        tab.add_handler(cdp.network.RequestWillBeSent, on_request)
        await tab.send(cdp.network.enable())

        print(f"[query] opening {BASE}/home")
        await tab.get(f"{BASE}/home")
        await tab.wait(5)
        _write("home.html", await tab.get_content())

        # type into the live search box to trigger the vrf'd search call
        js_type = (
            "(() => {"
            "  const i = document.querySelector("
            "    '.search-inner input[name=keyword], input[name=keyword], "
            "     input[type=search]');"
            "  if (!i) return 'NO_INPUT';"
            f"  i.value = {json.dumps(query)};"
            "  i.dispatchEvent(new Event('input', {bubbles:true}));"
            "  i.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}));"
            "  return 'OK:' + (i.name || i.type);"
            "})()"
        )
        result = await tab.evaluate(js_type)
        print(f"[query] search box: {result}")
        await tab.wait(5)

        _write("ajax_log.txt", "\n".join(ajax_urls) or "(no ajax calls seen)")

        if search_url["u"]:
            print(f"[query] refetching {search_url['u']}")
            try:
                _write("search_ajax.json", await _fetch_in_page(tab, search_url["u"]))
            except Exception as e:
                _write("search_ajax.json", f"FETCH FAILED: {e}")
        else:
            _write("search_ajax.json", "(ajax/manga/search was never fired)")

        # fallback: the plain filter page, rendered
        print(f"[query] opening filter page as fallback")
        await tab.get(f"{BASE}/filter?keyword={query.replace(' ', '+')}")
        await tab.wait(6)
        _write("search_results.html", await tab.get_content())
    finally:
        browser.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture real MangaFire artifacts.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--slug", help="manga slug, e.g. ad-astra-scipio-and-hanniball.lww3")
    g.add_argument("--query", help="search terms, e.g. 'ad astra'")
    args = ap.parse_args()

    if args.slug:
        nd.loop().run_until_complete(probe_slug(args.slug))
    else:
        nd.loop().run_until_complete(probe_query(args.query))

    print("\nDone. Hand back the files in probe_out/ for grounded parser code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
