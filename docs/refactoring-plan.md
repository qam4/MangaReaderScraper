# MangaReaderScraper — Refactoring Plan

Status: proposal / design note. Captures concrete findings from working on
the MangaFire parser (the parser rewrite, the network probe, and the test
suite that exposed several import-time breakages).

This is written against the code as of the `mangafire-parser-rewrite` branch.
It is a map for a cleanup, not a finished spec — but every item below is
grounded in a specific file, not generic advice.

---

## 1. Goals

1. **Make adding a new site a recipe, not archaeology.** Today it requires
   editing four files and reverse-engineering which fetch backend works.
2. **Share the repeated parser scaffolding** instead of copy-pasting it eight
   times.
3. **Stop the bleeding on imports / deps** so the test suite runs on a modern
   Python in seconds without a browser installed.
4. **Delete dead weight** that actively breaks things (the upload feature, the
   `undetected_chromedriver` dependency, dead sources).

Non-goals: resurrecting every dead site, building a plugin-loading system,
100% type coverage. This is a single-user tool; right-size the effort.

---

## 2. What's good (keep)

- The **`BaseSiteParser` / `BaseMangaParser` / `BaseSearchParser` split** is a
  sound shape. A site = a manga parser + a search parser, wired by a site
  parser. Keep this.
- The **`page_data` retry + Pillow validation + placeholder-page-on-failure**
  in `base.py` is genuinely good: failed pages become a "Page N missing"
  image rather than crashing the volume. Keep and reuse.
- The **`(page_num, bytes, status)` 3-tuple** flowing into `manga.py`'s
  `ThreadPool`, and the per-volume `Pool` parallelism, work. Don't churn them.
- The **CBZ/PDF save methods** in `manga.py` are fine.

## 3. What's bad (the core problems)

### 3.1 Stringly-typed fetch dispatch — the root of most mess

`utils.get_html_from_url(url, type="selenium")` is a 5-way `if/elif` over
`"requests" | "cloudscraper" | "uc" | "selenium" | "nodriver"`. Consequences:

- Every parser picks a backend by magic string, inline, with no type safety.
- Each parser re-implements Cloudflare handling and "is this a 404" logic.
- Several `_scrape_volume` methods catch `requests.exceptions.HTTPError`
  **even when the fetch went through selenium/nodriver**, which never raise
  it. That error handling is dead code (see `mangabuddy.py`, `mangafire.py`
  pre-rewrite).
- `import undetected_chromedriver as uc` sits at **module top of `utils.py`**,
  so importing *anything* drags in uc — which fails on Python ≥3.12
  (`distutils` removed). This is why the whole test suite can't import on a
  clean modern interpreter.

### 3.2 Adding a source touches four places

To add MangaFire I had to edit:
- `scraper/parsers/types.py` — four separate `Union[...]` lists
- `scraper/__main__.py` — the `get_manga_parser` dict **and** the `--source`
  argparse `choices` set
- `scraper/parsers/mangafire.py` — the parser itself
- (and `tests/helpers.py` if it should be in `ALL_PARSERS`)

Three of those four are pure boilerplate that should be automatic.

### 3.3 Untyped result dict

`SearchResults = Dict[str, Dict[str, str]]`. Every parser builds
`{"title": ..., "manga_url": ..., "chapters": ..., "source": ...}` by hand,
and the menu reads those string keys back. One typo = silent wrong column.

### 3.4 Search assumes HTML scraping

`BaseSearchParser._scrape_results(url, div_class)` bakes in "fetch a page,
find `<div class=X>`". MangaFire's search is a vrf-token-gated JSON ajax call
that doesn't fit this at all — which is exactly why search was the hardest
part of the rewrite and why the old parser just returned `{}`.

### 3.5 Dead weight breaking imports

- **Upload feature** (dropbox / pcloud / mega): you confirmed it never really
  worked. Worse, `scraper.__main__` imports `DropboxUploader`/`PcloudUploader`
  at module top, so `__main__` won't import without `dropbox` installed — and
  the test suite's autouse fixture patches `scraper.__main__.CONFIG`, so a
  missing `dropbox` breaks **every test**, not just upload tests.
- **`undetected_chromedriver`**: superseded by `nodriver` (same author) for
  this project's needs, and it's the dep that breaks on modern Python.
- **Dead sources**: `__main__` literally comments `mangareader # dead`,
  `mangafast # dead`. They linger in the unions and choices.

### 3.6 Dependency soup

`requirements.txt` carries `requests`, `cloudscraper`, `selenium`,
`undetected_chromedriver`, `nodriver`, **and** `curl_cffi` — multiple tools
for the same job, unpinned, flat (no dev/runtime split). No `pyproject`
dependency groups, no documented venv (so installs land in whatever Python is
on PATH — e.g. a global pyenv).

### 3.7 Test coupling

`conftest.py` imports `scraper.manga`, which imports `scraper.parsers.types`,
which imports **every** parser, which imports `base`, which imports `utils`,
which imports `undetected_chromedriver` at module top. So a unit test of pure
HTML parsing transitively needs reportlab, dropbox, a chromedriver shim, and
lxml all present. That's why the suite is fragile.

---

## 4. Target architecture

### 4.1 A Fetcher abstraction (the keystone)

Replace the `get_html_from_url(url, type=...)` string switch with a small
protocol and lazy-imported implementations:

```python
class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...

class FetchResult:
    status: int
    text: str
    # optional: cookies, final_url, json()
```

Concrete fetchers, each importing its heavy dep **lazily inside the class**,
never at module top:
- `RequestsFetcher` (plain, fast path)
- `CloudscraperFetcher`
- `BrowserFetcher` (nodriver) — also exposes `fetch_json_in_page(url)` and
  `capture_xhr(predicate)`, the two primitives the MangaFire work actually
  needed (intercept an ajax call, re-fetch it inside the page).

A parser declares the fetcher it wants (`fetcher = BrowserFetcher`) instead of
passing magic strings. Backends are imported only when instantiated, so
importing a parser never pulls in a browser driver. This alone fixes 3.1, the
uc-on-import breakage (3.5), and most of the test coupling (3.7).

Retire `undetected_chromedriver` and (probably) `selenium` once `BrowserFetcher`
covers their cases via nodriver.

### 4.2 Typed result model

```python
@dataclass
class SearchResult:
    title: str
    slug: str            # was manga_url
    latest_chapter: str
    source: str
```

`search()` returns `list[SearchResult]`; the menu indexes them. Kills 3.3.

### 4.3 Source registry

```python
@register_source("mangafire")
class Mangafire(BaseSiteParser): ...
```

A registry dict populated by the decorator. `--source` choices and the
`get_manga_parser` lookup both read from the registry. Adding a site = create
one file + decorate. The giant `Union[...]` blocks in `types.py` largely
disappear (use the base classes for typing). Kills 3.2.

### 4.4 Parser base offers both shapes

`BaseSearchParser` should provide two helpers and force neither:
- `parse_html_cards(html, selector)` — for scraping sites
- `parse_json(...)` — for ajax/JSON sites (MangaFire, increasingly common)

Same for chapter lists: HTML-page scrape vs. ajax endpoint. The MangaFire
parser becomes the reference example of the JSON path.

---

## 5. The probe: promote it to a first-class tool

The thing that actually made MangaFire tractable was **capturing real network
traffic**, not guessing selectors. Ship that workflow:

- `scraper probe <url>` subcommand (productized `mangafire_probe.py`): drives a
  browser, logs every ajax URL, dumps captured JSON/HTML into
  `tests/test_files/<site>/`.
- These captures become **test fixtures**. The MangaFire tests already parse
  the real captured `chapter_list.json` / `search.json` — that's the pattern:
  tests verify parsing against real responses, not assumptions.

### "Add a source" checklist (goes in CONTRIBUTING / docs)

1. `scraper probe <a manga page URL>` and `scraper probe --search <terms>`.
2. Read `ajax_log.txt` to find the search / chapter-list / page-list
   endpoints (or confirm it's plain HTML).
3. Save the captured responses into `tests/test_files/<site>/`.
4. Implement the manga + search parsers (copy MangaFire for a JSON site,
   MangaFast for an HTML site).
5. `@register_source("<site>")` on the site parser. Done — no other files.
6. Write tests that parse the captured fixtures.

---

## 6. Better practices (unglamorous, high payoff)

- **`pyproject.toml` with dependency groups** (runtime vs. dev), pinned
  versions, and a documented **venv**. Stop relying on PATH Python.
- **Prune deps**: drop `undetected_chromedriver`; decide on `selenium` and
  `cloudscraper` once `BrowserFetcher` lands; keep `nodriver` + `curl_cffi`
  (both proven by the MangaFire work).
- **Quarantine or remove upload**: move dropbox/pcloud/mega behind an optional
  extra (`pip install .[upload]`) with lazy imports, OR delete it. Either way,
  `__main__` must import without those packages.
- **Consistent error semantics**: parsers raise typed exceptions
  (`VolumeDoesntExist`, `MangaDoesNotExist`); the downloader decides policy.
  No more `return None` vs. raise vs. catch-an-error-that-never-fires.
- **Unit tests run with zero browser/network deps.** With the Fetcher
  abstraction, parser tests mock the fetcher and parse fixtures. Mark
  browser-touching tests as integration and make them skippable. (Right now
  `test_mangafast` triggers real selenium and takes ~17 min; that should be an
  opt-in integration test, not part of the default unit run.)
- **CI green on a pinned modern Python**, unit tests in seconds.
- **Kill `nodriver` UTF-in-comment patch friction**: pin a known-good
  `nodriver` version, or vendor the one-line patch, and note it in docs.

---

## 7. Suggested phasing

1. **Stop the bleeding.** Make heavy imports lazy (move `import
   undetected_chromedriver` out of `utils` module top; lazy-import upload in
   `__main__`). Suite imports on modern Python. Low risk, high relief.
2. **Fetcher abstraction.** Introduce `Fetcher` + concrete backends; migrate
   one parser (MangaFire — already browser-based) as the proof.
3. **Typed `SearchResult` + source registry.** Migrate parsers one at a time;
   shrink `types.py`.
4. **Probe subcommand + "add a source" docs**, with MangaFire as the example.
5. **Dependency + packaging cleanup** (`pyproject`, pinning, prune, venv).
6. **Dead code removal** (dead sources, upload decision) once nothing depends
   on them.
7. **CI + test tiering** (unit vs. integration).

Each phase leaves the tool working. Phase 1 is the cheapest and removes the
most day-to-day pain.

---

## 8. Reference: the MangaFire rewrite as the worked example

`scraper/parsers/mangafire.py` (this branch) already demonstrates the target
patterns for a JSON-ajax site:
- chapter list via `/ajax/manga/<id>/chapter/en` (id = slug after last `.`)
- page list via intercepting `ajax/read/chapter` then re-fetching in-page
- images via curl_cffi (Chrome impersonation) + browser cookies + Referer
- descramble ported from the keiyoushi Tachiyomi extension's ImageInterceptor
- search via the vrf-gated `ajax/manga/search` (type into the live box,
  intercept the call)
- tests parse real captured responses in `tests/test_files/mangafire/`

Use it as the template when building the Fetcher abstraction and the
"add a source" recipe.
