# Adding a manga source

Supporting a new site is inherently manual — you can't know a site's structure
until you look at it. The tooling here exists to make the *looking* fast and the
*writing* small, not to automate it away. The workflow below is the same one
used to build the MangaFire parser; it's the documented way to add a source.

## The loop

### 1. Probe the site

Run the probe against a manga page and a search/home page:

```bash
python -m scraper.probe https://example.com/manga/<slug>
python -m scraper.probe https://example.com/home --site example
```

For each URL it drives a real browser (so Cloudflare clears) and writes into
`tests/test_files/<site>/`:

- **`ajax_log.txt`** — every ajax/API/JSON URL the page fired. This alone often
  hands you the search, chapter-list, and page-list endpoints.
- **`candidates.txt`** — heuristic selector candidates: chapter-looking links
  (`chapter-<num>`), elements carrying `data-number` / `data-src`, and the
  largest `<img>` cluster (usually the reader's page-image container).
- **`page.html`** — the rendered HTML, for when you need to look closer.

### 2. Identify the mechanisms

From `ajax_log.txt` and `candidates.txt`, work out three things:

- **search**: is there an `ajax/.../search` call (JSON), or is it plain HTML?
- **chapter list**: a JSON endpoint (like MangaFire's
  `/ajax/manga/<id>/chapter/en`) or chapter links in the HTML?
- **page images**: in the HTML (`data-src` lazy images), or behind an ajax call
  (like MangaFire's `ajax/read/chapter`)?

### 3. Save fixtures

Keep the captured responses you'll parse (the chapter-list JSON, a search
response, a rendered volume page) under `tests/test_files/<site>/`. These become
both your reference *and* your test inputs.

### 4. Write the parser

Create `scraper/parsers/<site>.py` with a `<Site>MangaParser`,
`<Site>Search`, and `<Site>` site parser. Two patterns to copy:

- **JSON/ajax site** → copy `mangafire.py`. It drives the browser via
  `BrowserFetcher` (`capture_xhr` to intercept a vrf-gated call,
  `fetch_json_in_page` for a known endpoint).
- **plain-HTML site** → copy `mangareader.py` / `mangakaka.py`. They use
  `fetch_soup(url)` (plain requests) — or `fetch_soup(url, BrowserFetcher())`
  for a JS-rendered page (see `mangabuddy.py` / `mangapark.py`).

Use the shared building blocks:
- `scraper.fetchers` — `fetch_soup`, `RequestsFetcher`, `CloudscraperFetcher`,
  `BrowserFetcher`. Never call a browser/HTTP library directly.
- `scraper.selection.sort_chapter_ids` — for ordering `all_volume_ids`. Don't
  roll your own `sorted(key=float)` / string compare.
- `scraper.new_types.SearchResult` — return these from search, not raw dicts.

### 5. Register the source

Decorate the site parser and add its module to the registry's module list:

```python
from scraper.registry import register_source

@register_source("example")
class Example(BaseSiteParser): ...
```

Then add `"scraper.parsers.example"` to `_SOURCE_MODULES` in
`scraper/registry.py`. That's it — `--source example` and `get_manga_parser`
pick it up automatically; there's no `get_manga_parser` dict, argparse `choices`
list, or `types.py` Union to edit.

### 6. Write tests against the fixtures

Parse the captured `tests/test_files/<site>/` responses in
`tests/test_<site>.py`. Mock the fetch seam (`scraper.parsers.<site>.fetch_soup`,
or `BrowserFetcher.capture_xhr` / `fetch_json_in_page`) so tests never touch the
network. Keep them in the default (unit) tier; tag anything that genuinely needs
a live browser with the `integration` marker.

## Shortcut: engine reuse

Many sites are reskins of the same backend (the manganelo/natomanga/mangakaka
family share markup). If a new site matches one you already support, you may be
able to point a new `base_url` at an existing parser instead of writing a new
one. This only helps when the engine is already supported — a genuinely new
site still needs the full loop above.
