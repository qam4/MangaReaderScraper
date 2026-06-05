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
python -m scraper.probe https://example.com/home --search "naruto"
```

For each URL it drives a real browser (so Cloudflare clears) and writes into
`probe_out/<site>/` (gitignored scratch -- promote a curated subset to
`tests/test_files/<site>/` by hand):

- **`ajax_log.txt`** — every ajax/API/JSON URL the page fired. This alone often
  hands you the search, chapter-list, and page-list endpoints. Some sites are
  API-backed (call the JSON directly, MangaFire-style); some are plain HTML.
  The probe reports what it sees rather than assuming.
- **`api_index.txt`** + **`api_*.json`** — the JSON *response bodies* of those
  API calls, dumped to disk so you can read the actual shape (field names for
  title/slug/chapters/images) instead of reverse-engineering markup. The index
  maps each dump file back to its source URL. This is usually where you find the
  search and chapter-list APIs and learn their exact schema.
- **`api_backends.txt`** — for each captured API endpoint, the probe re-hits it
  with **plain requests** *and* **curl_cffi** (Chrome impersonation) and reports
  whether each got real JSON. This tells you whether the API is reachable
  without a browser: if curl_cffi shows "JSON OK", the parser can use
  `CurlCffiFetcher` (no browser) for that endpoint. (Note: curl_cffi's TLS
  impersonation is far stronger than plain requests and often clears Cloudflare
  walls that `fetch_recommendation.txt` -- which only tries plain requests --
  flags as needing a browser. Always check `api_backends.txt` before assuming a
  site needs nodriver.)
- **`fetch_recommendation.txt`** — fetches the page with plain requests AND a
  browser, then recommends the *gentlest* strategy that works: `requests` →
  `nodriver-headless` → `nodriver-headful` → `nodriver-manual` (an interactive
  captcha you solve once by hand) → `unknown`. Use the cheapest one; don't
  hammer a site with requests if it's challenged (that's how you get banned).
  This compares *plain requests* vs a browser -- so it can under-sell curl_cffi;
  cross-check `api_backends.txt`.
- **`candidates.txt`** — heuristic selector candidates (chapter-looking links,
  `data-number` / `data-src` elements, the largest `<img>` cluster) plus
  challenge-wall vs. behind-Cloudflare detection. These heuristics are derived
  from sites we've seen; on a novel layout they may find little — then open
  `page.html` directly.
- **`recommendation.txt`** — a synthesized, **advisory** single summary of the
  recommended approach: the host(s) seen, whether an open JSON API was found,
  the suggested default fetcher, and the candidate search / chapter-list / image
  mechanisms (each with a suggested fetcher and any gotcha note). It reads the
  artifacts above for you so you don't have to cross-read five files — **read it
  first**, then cross-check it against the detailed files. It phrases everything
  as suggestions to confirm against the live site, not as decisions.
- **`page.html`** — the rendered HTML, for manual inspection.

(A `--map-by-example` run additionally writes **`field_map.txt`** — see below.)

#### `--find`: locate a selector by content

When the heuristics don't pinpoint the chapter list / image container (sites use
arbitrary, sometimes obfuscated class names — there is no universal selector),
use **`--find`** with a string you can *see* on the page (a chapter number, a
title, a slug):

```bash
python -m scraper.probe https://example.com/manga/<slug> --find "165"
```

It writes `find.txt` listing every element whose text or attribute contains the
string, with the element's selector and its ancestor path, e.g.:

```
[text] table.listing > tr > td > a.chico
    'Chapter 165'
```

→ now you know the chapter anchor is `a.chico` inside `table.listing`. You supply
the known value (from your eyes); the probe does the tedious locating. It does
not decide which match is "the right one" — you interpret the short list.

#### `--map-by-example`: locate a value inside captured JSON

`--find` locates a visible string in the page's **HTML**. Its JSON counterpart
is **`--map-by-example`**: on an API-backed site (MangaFire, mangak.io) the
title/slug/chapter/image data lives in JSON bodies, not markup, and those bodies
run to thousands of lines. Give it the values you can *see* on the page as
`name=value` pairs and it reports the JSON *path* each one lives at:

```bash
python -m scraper.probe https://mangak.io/naruto --map-by-example title=Naruto slug=naruto chapter=700.5
```

This is a standalone, browser-free mode: it reads the captures already in
`probe_out/<site>/` and never re-hits the site. So **capture first** with a
normal probe run, then re-run with `--map-by-example` (if `field_map.txt` comes
out empty, you probably haven't probed the page yet). It searches every
`api_*.json` body in the output dir *and* the embedded `__NEXT_DATA__` extracted
from `page.html`, then writes `field_map.txt`:

```
# Field map (advisory -- all matches listed; you pick the field)

title = 'Naruto'  (1 match(es))
  [exact] api_01_search.json: data.items[0].name
      leaf='Naruto'

slug = 'naruto'  (2 match(es))
  [exact] api_01_search.json: data.items[0].slug
      leaf='naruto'
  [path-prefix] api_01_search.json: data.items[0].url
      leaf='/naruto'

chapter = '700.5'  (1 match(es))
  [substring] api_01_search.json: data.items[0].latest_chapters[0].name
      leaf='Chapter 700.5 : Uzumaki Naruto'
  !! hint: sibling 'chapter_number'=748 disagrees with 700.5 in the matched value; it is likely a sequence counter rather than the displayed number -- derive the number from the matched field instead
     (sibling data.items[0].latest_chapters[0].chapter_number)
```

The match kinds tell you *how* a value was found:

- **`exact`** — a string leaf identical to your value.
- **`substring`** — a string leaf that *contains* it (e.g. `700.5` inside
  `'Chapter 700.5 : Uzumaki Naruto'`).
- **`numeric`** — leaf and value denote the same number across types
  (`748` matches `"748"`, `"748.0"` matches `"748"`).
- **`path-prefix`** — a URL-ish leaf equal apart from a leading `/`
  (`/naruto` vs `naruto`).

The **sibling-mismatch hint** (the `!!` line) is the gotcha catcher: when your
value contains a number and a *neighbouring* field looks like it should hold the
same number but disagrees, it's flagged. Above, the chapter's displayed number
is `700.5` but the adjacent `chapter_number` is `748` — the mangak.io sequence
counter, exactly the trap that would make `--volumes` ranges wrong. The hint
never asserts the sibling is wrong; it points you at the disagreement so you
derive the number from the right field.

Like `--find`, the output is advisory: it lists **all** matches and never picks
"the" field — you read the short report and decide. And because it only reads
artifacts already on disk, it adds no extra load on the site (in keeping with the
project's "probe once, be realistic" ethos).

### 2. Identify the mechanisms

From `ajax_log.txt`, `api_index.txt` / `api_*.json`, `api_backends.txt`,
`candidates.txt`, `find.txt` and `field_map.txt`, work out three things:

- **search**: is there a JSON search API (e.g. `api.example/titles/search`, or
  an `ajax/.../search` call), or is it plain HTML? Check `api_*.json` for the
  response shape.
- **chapter list**: a JSON endpoint (like mangak.io's `/titles/<id>/chapters`
  or MangaFire's `/ajax/manga/<id>/chapter/en`) or chapter links in the HTML?
- **page images**: a JSON endpoint, embedded in the page's Next.js
  `__NEXT_DATA__` (like mangak.io), `data-src` lazy images, or behind an ajax
  call (like MangaFire's `ajax/read/chapter`)? If you can't find an image
  endpoint in `ajax_log.txt`, the site likely server-renders them into the page
  — read them from the HTML/embedded JSON.

Then check `api_backends.txt` to pick the fetcher: an open API → `CurlCffiFetcher`
(no browser); a Cloudflare-walled page that curl_cffi still clears → also
`CurlCffiFetcher`; only reach for `BrowserFetcher` when curl_cffi is genuinely
challenged.

### 3. Save fixtures


Keep the captured responses you'll parse (the chapter-list JSON, a search
response, a rendered volume page) under `tests/test_files/<site>/`. These become
both your reference *and* your test inputs.

### 4. Write the parser

Create `scraper/parsers/<site>.py` with a `<Site>MangaParser`,
`<Site>Search`, and `<Site>` site parser. Three patterns to copy:

- **Open-API site (best case)** → copy `mangabuddy.py` (mangak.io). The site is
  a JS app backed by a public JSON API (`api.mangak.io/titles/search`,
  `/titles/<id>/chapters`). Hit it directly with `CurlCffiFetcher` — no browser.
  Worked example end to end:
  - search returns `{data:{items:[{id, slug, name, stats.chapters_count,
    latest_chapters:[…]}]}}`;
  - `--manga <slug>` only gives the slug, so resolve slug → API `(id, cv)` via
    the search endpoint, then call `/titles/<id>/chapters?cv=<cv>`;
  - the page-image list had **no** public endpoint (verified by intercepting
    every XHR) — it lives in the chapter page's server-rendered Next.js
    `__NEXT_DATA__`. `page_urls` fetches the page with `CurlCffiFetcher` and
    parses that embedded JSON, falling back to `BrowserFetcher` only if
    curl_cffi is challenged.
  - **gotcha:** the API's `chapter_number` is a sequence counter, not the
    displayed number — parse the real number from the chapter *name*
    (`"Chapter 700.5"` → `700.5`), or `--volumes` ranges will be wrong.
- **JSON/ajax site behind a vrf token** → copy `mangafire.py`. It drives the
  browser via `BrowserFetcher` (`capture_xhr` to intercept a vrf-gated call,
  `fetch_json_in_page` for a known endpoint).
- **plain-HTML site** → copy `mangareader.py` / `mangakaka.py`. They use
  `fetch_soup(url)` (curl_cffi by default) — or `fetch_soup(url,
  BrowserFetcher())` for a JS-rendered page.

Use the shared building blocks:
- `scraper.fetchers` — `fetch_soup`, `CurlCffiFetcher` (the default, Chrome TLS
  impersonation), `RequestsFetcher`, `CloudscraperFetcher`, `BrowserFetcher`.
  Prefer the cheapest one `api_backends.txt` says works; never call a
  browser/HTTP library directly.
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
