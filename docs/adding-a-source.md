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

### 4b. (Optional) Generate the parser from a confirmed config — Step B

Once `--map-by-example` has confirmed the field map (Step A), you can skip most
of the boilerplate for an **API-backed** site by recording that map in a small
`parser.toml` and generating the parser + tests from it:

```bash
# preview only -- writes nothing, prints both sources to stdout
python -m scraper.scaffold parser.toml --dry-run

# write scraper/parsers/<register_as>.py + tests/test_<register_as>.py
python -m scraper.scaffold parser.toml \
    --search search_naruto.json \
    --chapters chapters_naruto.json \
    --page-html chapter_page.html
```

The generated parser is structurally the shipped `mangabuddy.py`: it wires the
configured fetcher, `sort_chapter_ids`, `SearchResult`, the
number-from-chapter-name parsing, and `get_by_path` — never a direct HTTP/browser
call on the data path. The generated tests run against the fixtures you name and
assert non-empty/specific results, so a wrong field path fails loudly rather than
mis-parsing silently.

This is a **shortcut for the open-API pattern**, not a replacement for the loop:
a vrf-token site (`mangafire.py`) or a plain-HTML site (`mangareader.py`) is
still hand-written.

#### The config schema

A complete mangak.io `parser.toml` (this is the worked example the generator was
built against):

```toml
site = "mangak.io"
register_as = "mangabuddy"
base_url = "https://mangak.io"
api_url = "https://api.mangak.io"
fetcher = "curl_cffi"

[search]
endpoint = "/titles/search?q={query}"
items = "data.items"
title = "name"
slug = "slug"

[chapters]
endpoint = "/titles/{id}/chapters?cv={cv}"
list = "data.chapters"
chapter_name = "name"
chapter_slug = "slug"
id_from = "id"
cv_from = "cv"

[images]
source = "next_data"
images_path = "pageProps.initialChapter.images"
page_url = "{base_url}/{slug}/{chapter_slug}"
```

**Top level**

- `site` — human-facing site name, used in the generated docstrings.
- `register_as` — the name the parser registers under (`@register_source`), and
  the basename of the generated files (`scraper/parsers/<register_as>.py`,
  `tests/test_<register_as>.py`).
- `base_url` — site base URL for page requests.
- `api_url` — API base URL the `endpoint` templates below are relative to.
- `fetcher` — the chosen data fetcher: `curl_cffi`, `browser`, or `requests`
  (pick the cheapest one `api_backends.txt` says works).

**`[search]`**

- `endpoint` — search endpoint template (relative to `api_url`) with a
  `{query}` placeholder, e.g. `/titles/search?q={query}`.
- `items` — JSON path to the array of result items, e.g. `data.items`.
- `title` — path *within one item* to the display title.
- `slug` — path *within one item* to the manga slug. This is also the field
  matched against the requested slug during slug→id resolution.

**`[chapters]`**

- `endpoint` — chapters endpoint template with `{id}` / `{cv}` placeholders,
  e.g. `/titles/{id}/chapters?cv={cv}`.
- `list` — JSON path to the array of chapters.
- `chapter_name` — path *within one chapter* to its display name (the human
  chapter number is parsed from this, not from any API number field — see the
  mangak.io gotcha above).
- `chapter_slug` — path *within one chapter* to its url slug.
- `id_from` / `cv_from` — these express **slug→id resolution**: the requested
  slug only identifies a title, so search is hit first, the item whose `slug`
  matches is found, and `id_from` / `cv_from` name the fields read off *that
  item* to fill the chapters endpoint's `{id}` / `{cv}`.

**`[images]`** — `source` selects one of the three observed image modes, and the
other fields it needs depend on it:

- `source = "api"` → `endpoint` (standalone image-list API template) +
  `images_path` (JSON path to the images array in that response).
- `source = "next_data"` → `page_url` (chapter-page URL template with
  `{base_url}` / `{slug}` / `{chapter_slug}`) + `images_path` (JSON path to the
  images array inside the page's embedded `__NEXT_DATA__`).
- `source = "html"` → `page_url` + `selector` (CSS selector for the image
  elements). **The `html` mode is not auto-generated**: `page_urls` is emitted
  as an explicit `NotImplementedError` hook for you to implement, since
  lazy-loading / `data-src` / descramble quirks can't be derived.

#### It's a scaffold — verify it

The generated parser and tests are a **SCAFFOLD requiring live verification**.
The JSON *shapes* are confirmed from your fixtures, but the **live network paths
are unverified**. After generating you must:

1. run the generated tests (they exercise the parsing logic against the
   fixtures);
2. verify against the live site (does the fetcher actually reach the API, does
   the image CDN need cookies);
3. implement any `NotImplementedError` hooks the scaffold emits — `descramble` /
   vrf-token / the `html` image mode;
4. **add the new module to `_SOURCE_MODULES` in `scraper/registry.py`** —
   registration requires that list, so `--source <name>` won't see the parser
   until you add `"scraper.parsers.<register_as>"` there (the generator prints
   this reminder).

**Safety flags:** `--dry-run` / `--print` writes nothing and prints both sources
to stdout; `--out-dir DIR` writes under an arbitrary sandbox directory instead
of the repo; and a generate **never overwrites an existing file** unless you
pass `--force` (so it can't clobber a shipped parser). `--fixtures-dir` overrides
the fixtures directory the generated tests read from (default
`tests/test_files/<register_as>`).

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
