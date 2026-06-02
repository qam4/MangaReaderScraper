# MangaReaderScraper — Sanitization & Refactoring Plan

Status: proposal / design note. Captures concrete findings from working on
the MangaFire parser (the parser rewrite, the network probe, and the test
suite that exposed several import-time breakages).

Written against the code on the `mangafire-parser-rewrite` branch. It is a map
for a cleanup, not a finished spec — but every item is tied to a specific file,
not generic advice.

---

## 0. The honest framing (read this first)

There are **two independent problems** here, and earlier drafts of this plan
conflated them. They need separating because the fixes are different and one of
them is partly unsolvable.

### Problem A — adding a site is manual, and largely *has* to be

You cannot know a site's structure until you look at it. For every new site
someone must: open it, find where the chapter list / volume list / page images
live, and write the site-specific extraction. **No refactor and no tool removes
this.** Supporting a site whose internals you don't know in advance is, in the
general case, not automatable.

What tooling *can* do is shrink the manual core and remove the friction around
it:
- make the **looking** faster (a probe that surfaces candidate endpoints and
  selectors instead of you scrolling through 74KB of HTML);
- make the **writing** smaller (base helpers so a new parser is ~20 lines of
  selectors, not ~120 lines of copy-pasted scaffolding);
- **sidestep** the work entirely for sites that secretly run a known engine
  (see §4.2 — the one real multiplier).

So the realistic goal is "shrink and de-friction the manual loop," not
"automate adding sites." This doc is careful not to promise the latter.

### Problem B — the codebase has accumulated friction and inconsistency

This is the fixable part, and it's worth doing on its own merits even though it
doesn't make new sites effortless: stringly-typed fetch dispatch, no shared
domain model (the chapter-id saga, §3.1), four files to register a source,
dead code that breaks imports, dependency soup. Cleaning this up makes the tool
pleasant to work in and the tests runnable. It's orthogonal to Problem A.

**Two tracks, neither a silver bullet:**
- **Workflow** (§4–§5): smarter probe, engine reuse, base helpers — shrinks the
  manual loop.
- **Hygiene** (§3, §6): domain model, fetch abstraction, registry, deps, dead
  code — makes the codebase sane and the tests fast.

---

## 1. Goals

1. Shrink and de-friction the manual "add a site" loop (accepting it can't be
   eliminated).
2. Establish **one domain model** so site differences don't ripple into five
   files.
3. Share the repeated parser scaffolding instead of copy-pasting it eight times.
4. Stop the import/dependency bleeding so tests run on a single pinned modern
   Python in seconds without a browser.
5. Delete dead weight that actively breaks things.

Non-goals: supporting arbitrary unknown sites automatically, a plugin-loading
system, 100% type coverage. Single-user tool; right-size the effort.

---

## 2. What's good (keep)

- The **site / manga / search parser split** is a sound shape. Keep it.
- `page_data`'s **retry + Pillow validation + placeholder-page-on-failure**
  (in `base.py`) is genuinely good — a failed page becomes a "Page N missing"
  image instead of crashing the volume. Keep and reuse.
- The **`(page_num, bytes, status)` tuple** flowing into `manga.py`'s
  `ThreadPool`, and per-volume `Pool` parallelism, work. Don't churn them.
- The CBZ/PDF writers in `manga.py` are fine.

---

## 3. What's bad (the fixable friction)

### 3.1 No single domain model — the chapter-id saga (canonical symptom)

The chapter/volume identifier has been reworked repeatedly (int → float →
string + natural sort) because different sites number chapters differently
(decimals like `9.22`, `28.22`; gaps; volume-vs-chapter). The result is that
**three different orderings and an int-only selector coexist in the tree right
now**:

- `scraper/__main__.py` `get_volume_values`: `range(int(start), int(end)+1)`
  — the `--volumes 9-12` range parser is **integer-only**. It silently cannot
  express decimal chapters (the `9.22` / `28.22` we saw on MangaFire).
- `scraper/manga.py` `Volume.number` is a **`str`**, and volumes are sorted
  with **`natural_sort`** (alphanumeric).
- `scraper/parsers/mangafire.py` `all_volume_ids` sorts with **`float(v)`**.
- `scraper/parsers/mangafast.py` `all_volume_ids` compares
  **`vol <= highest_volume` as strings** (lexicographic — `"9" > "10"`).

This is the root symptom of having no shared notion of "chapter id." Every new
site with a different scheme forced a patch in whichever spot broke.

**Fix:** one `ChapterId` value object (or a single normalize+sort helper) that
all parsers, the selector parser, and the sorter use. It must handle: integers,
decimals (`9.22`), and the `start-end` range selector over non-integers.
Define ordering once; forbid ad-hoc `float()`/`int()`/string sorts elsewhere.

### 3.2 Stringly-typed fetch dispatch

`utils.get_html_from_url(url, type="selenium")` is a 5-way `if/elif` over
`"requests" | "cloudscraper" | "uc" | "selenium" | "nodriver"`:
- parsers pick a backend by magic string, inline, no type safety;
- each parser re-implements Cloudflare and 404 handling;
- several `_scrape_volume` methods catch `requests.exceptions.HTTPError`
  **even when the fetch went through selenium/nodriver**, which never raise it
  — dead error-handling (see `mangabuddy.py`, pre-rewrite `mangafire.py`);
- `import undetected_chromedriver` sits at **module top of `utils.py`**, so
  importing anything pulls in uc — which fails on Python ≥3.12 (`distutils`
  removed). This is why the suite can't import on a clean modern interpreter.

### 3.3 Adding a source touches four places

For MangaFire I edited: `parsers/types.py` (four `Union[...]` lists),
`__main__.py` (the `get_manga_parser` dict **and** the `--source` argparse
`choices`), the parser itself, and `tests/helpers.py`. Three of four are pure
boilerplate that should be automatic.

### 3.4 Untyped result dict

`SearchResults = Dict[str, Dict[str, str]]`. Every parser hand-builds
`{"title","manga_url","chapters","source"}` and the menu reads those keys back.
One typo = silent wrong column.

Concrete symptom (mangabuddy `_extract_text`): the `chapters` field is meant to
carry the latest-chapter number for the "Latest Volume" column, but mangabuddy
stuffs the raw `latest-chapter` text (`"Chapter 100: Title"`) in without the
`re.sub(r"\D", "", ...)` the other parsers apply, and falls back to the **int**
`0` when absent — violating the `Dict[str, str]` type the menu then indexes as a
string. Two lessons: (a) "latest chapter" is a display string, not a reliable
chapter *count*/ordering signal (slug/number schemes vary per site — see §3.1
fallback); (b) the typed `SearchResult` (§5.3) should own this normalization so
one parser can't quietly emit the wrong type. Defer the mangabuddy fix to that
phase (its search is Cloudflare-broken anyway).

### 3.5 Search base assumes HTML scraping

`BaseSearchParser._scrape_results(url, div_class)` bakes in "fetch page, find
`<div class=X>`". MangaFire's search is a vrf-token-gated JSON ajax call that
doesn't fit at all — which is why search was the hardest part of the rewrite
and why the old parser just returned `{}`.

### 3.6 Dead weight breaking imports

- **Upload** (dropbox/pcloud/mega): confirmed never really worked. `__main__`
  imports the uploaders at module top, so it won't import without `dropbox`
  installed — and the test suite's autouse fixture patches
  `scraper.__main__.CONFIG`, so a missing `dropbox` breaks **every** test.
- **`undetected_chromedriver`**: superseded by `nodriver` (same author) for
  this project's needs; it's the dep that breaks on modern Python.
- **Dead sources**: `__main__` literally comments `mangareader # dead`,
  `mangafast # dead`, yet they linger in the unions and choices.

### 3.7 Dependency soup

`requirements.txt` carries `requests`, `cloudscraper`, `selenium`,
`undetected_chromedriver`, `nodriver`, **and** `curl_cffi` — multiple tools for
the same job, unpinned, flat (no dev/runtime split). No `pyproject` dependency
groups, no documented venv, so installs land in whatever Python is on PATH.

### 3.8 Test coupling

`conftest.py` → `scraper.manga` → `parsers.types` → every parser → `base` →
`utils` → top-level `undetected_chromedriver`. So a unit test of pure HTML
parsing transitively needs reportlab, dropbox, a chromedriver shim, and lxml.
That's why the suite is fragile. (Also: `test_mangafast` triggers a real
selenium run, ~17 min — it should be opt-in integration, not a unit test.)

---

## 4. Workflow track — shrink the manual loop (Problem A)

### 4.1 A smarter probe (surfaces candidates, doesn't just dump)

The thing that made MangaFire tractable was **watching the network**, not
reading HTML: the instant the probe printed `ajax_log.txt`, we had the search
and chapter endpoints with zero eyeballing. Make that the point of the tool.

`scraper probe <url>` should:
- log every ajax URL the page fires (this alone often hands you the API);
- for HTML sites, heuristically **surface candidate selectors**: links matching
  `chapter-[\d.]+`, the largest `<img>` cluster, elements carrying
  `data-number` / `data-src`;
- dump captured JSON/HTML into `tests/test_files/<site>/`.

Goal: turn "stare at 74KB of HTML" into "here are 5 candidates, pick one." It
doesn't eliminate the manual step — it makes the looking fast.

### 4.2 Parsers per *engine*, not per *site* (the one real multiplier)

Many manga sites are reskins of the same backends (Madara WordPress theme,
MangaStream/MangaReader engines, etc.). The codebase already half-knows this:
`manganelo.py` says *"Seems to be the same as manganelo.com — can probably use
this class for manganelo too,"* and `mangabuddy.py` echoes it.

If parsers were keyed to **engines**, a new site running a known engine becomes:
point a new `base_url` at the existing engine parser. Near-zero archaeology.
This is the closest thing to a real win — and it's honest about its limit: it
only helps when a new site happens to run an engine you already support. It
does not solve the genuinely-new-site case (Problem A remains).

### 4.3 Probe captures ARE the parser spec

You probe → it surfaces endpoints/selectors → you drop captures into fixtures →
you write the parser against real data → tests parse those captures. This is
exactly the MangaFire flow; make it the documented, only way to add a site.

#### "Add a source" checklist (for CONTRIBUTING/docs)
1. `scraper probe <manga page URL>` and `scraper probe --search <terms>`.
2. Read `ajax_log.txt` / candidate selectors to find search, chapter-list, and
   page-list mechanisms (or confirm plain HTML).
3. Save captured responses into `tests/test_files/<site>/`.
4. Implement manga + search parsers (copy MangaFire for a JSON/ajax site,
   MangaFast for an HTML site) — or just bind a `base_url` to an engine parser.
5. `@register_source("<site>")` on the site parser. No other files.
6. Write tests that parse the captured fixtures.

---

## 5. Hygiene track — make the codebase sane (Problem B)

### 5.1 One domain model first (`ChapterId`)

Do §3.1 before anything else — it's small and it stops the recurring patch.
A single value object with a defined ordering, used by parsers, the `--volumes`
range parser, and the sorter. Decimals and ranges-over-decimals handled in one
place.

### 5.2 Fetcher abstraction (supporting plumbing, not the headline)

Replace the `type=...` switch with a small protocol and lazy-imported backends:

```python
class Fetcher(Protocol):
    def get(self, url: str) -> FetchResult: ...

class FetchResult:
    status: int
    text: str
    # optional: cookies, final_url, json()
```

Backends (`RequestsFetcher`, `CloudscraperFetcher`, `BrowserFetcher`) import
their heavy dep **lazily inside the class**, never at module top. `BrowserFetcher`
(nodriver) also exposes `fetch_json_in_page(url)` and `capture_xhr(predicate)`
— the two primitives the MangaFire work actually needed. A parser declares
`fetcher = BrowserFetcher` instead of passing strings.

This is plumbing the probe and engine parsers rely on; it also fixes the
uc-on-import breakage (§3.6) and most test coupling (§3.8). Not the headline.

### 5.3 Typed `SearchResult` + source registry

```python
@dataclass
class SearchResult:
    title: str
    slug: str
    latest_chapter: str
    source: str

@register_source("mangafire")
class Mangafire(BaseSiteParser): ...
```

`--source` choices and `get_manga_parser` read from the registry. Adding a site
= one file + a decorator. The `Union[...]` blocks in `types.py` largely vanish.

### 5.4 Quarantine or remove upload

Move dropbox/pcloud/mega behind an optional extra (`pip install .[upload]`)
with lazy imports, OR delete it. Either way `__main__` must import without those
packages (unblocks the whole test suite).

### 5.5 Dependencies, packaging & environment (do this first — §6.0)

**Single Python version.** Drop the multi-version pretence. `tox.ini` declares
`envlist = py37,py38`, but CI already runs only 3.7 (the matrix is commented
out: *"py3.8 fails due to lxml error"*), and this plan is trying to move
*forward* to a modern interpreter (§3.6: uc breaks on ≥3.12). Supporting a
range is cost with no payoff for a single-user tool. **Target Python 3.13**
(3.13.5 confirmed available locally via pyenv-win); pin it and target only
that. Note the dev machine's default `python` is 3.14 — a `.python-version`
file pinning `3.13` keeps uv selecting the right interpreter regardless.

**Adopt `uv`.** It delivers exactly what this section needs — reproducible
pinned installs (`uv.lock`, killing the §3.7 soup), dependency groups
(runtime / dev / an optional `upload` extra to quarantine §5.4), and a fast
documented venv (`uv sync`). It also subsumes what `tox` was doing here: with a
single target, tox is just a task runner around `pyflakes/flake8/black/mypy/
pytest`, so replace it with `uv run <tool>` (in a Makefile/justfile or the
README). Keep tox only if a version matrix ever comes back (then `tox-uv`);
for now, delete it.

**Concrete moves:**
- `pyproject.toml` with `[project].dependencies` (migrated out of
  `requirements.txt`), `requires-python = ">=3.13"`, and
  `[project.optional-dependencies]` `dev` + `upload` groups. Retire `setup.py`
  in favour of the `[project]` table (build-backend is already setuptools).
  Add a `.python-version` pinning `3.13`.
- Pinned versions; `uv lock` committed.
- Prune: drop `undetected_chromedriver`; decide on `selenium`/`cloudscraper`
  once `BrowserFetcher` lands; keep `nodriver` + `curl_cffi` (both proven by
  MangaFire). Pin a known-good `nodriver` (note the UTF-in-comment patch needed
  on some Python versions) so it's not rediscovered each setup.
- Optional consolidation: replace `pyflakes`+`flake8`+`black`+`isort` with
  **ruff** (lint + format + import sort, one config block) — same
  reduce-overlapping-tools instinct as the fetch-lib pruning. mypy/pytest stay.
- Rewrite CI to `setup-uv` + `uv sync` + `uv run` the checks on the one Python.
- Refresh `.pre-commit-config.yaml` (pinned to python3.7 / black 19.10b0) or
  fold its hooks into ruff.

Why first: every later phase wants a sane, fast baseline to test against, and
the lazy-import work (§6.1) only pays off once the suite actually runs on a
clean modern interpreter.

### 5.6 Consistent error semantics

Parsers raise typed exceptions (`VolumeDoesntExist`, `MangaDoesNotExist`); the
downloader decides policy. No more `return None` vs. raise vs.
catch-an-error-that-never-fires.

### 5.7 Test tiering + CI

Unit tests mock the fetcher and parse fixtures — zero browser/network deps,
seconds to run. Browser-touching tests marked `integration` and skippable. CI
green on a pinned modern Python.

---

## 6. Suggested phasing

Each phase leaves the tool working.

0. **Environment & packaging baseline (do first)** — ✅ **DONE.** Pinned Python
   3.13 (`.python-version`); migrated to `uv` with `pyproject.toml`
   (`[project]` deps, `dev` group, `upload` extra) + `uv.lock`; consolidated
   `pytest.ini`/`mypy.ini`/tox lint config into `pyproject.toml`; deleted
   `tox.ini`, `setup.py`, `requirements.txt`, `dev-requirements.txt`; rewrote CI
   on `uv` + Python 3.13; refreshed `.pre-commit-config.yaml` and `.flake8`.
   Findings worth recording:
   - The §3.6 claim that the suite *can't import* on modern Python did **not**
     reproduce: `undetected_chromedriver` 3.5.5 imports fine on 3.13.5 (setuptools
     still vendors a `distutils` shim). The import-bleed risk is real but not yet
     fatal — Phase 1 (lazy imports) is still worth doing, just less urgent.
   - The real breakage is **test mocks targeting the wrong module**: every
     `test_page_data` patches `<module>.requests.get`, but `page_data` lives in
     `base.py` and uses `utils.request_session().get()` — so the mock misses and
     the test makes real network calls with retry/backoff (this is the "17-min
     mangafast" hang; it's actually all the `test_page_data` tests). Added
     `pytest-timeout` (60s default) so this fails fast instead of hanging.
   - bs4 upgrade surfaced two latent bugs: `mangakaka.all_volume_ids` (`.text`
     on a str) and ~35 mypy `union-attr` errors from `Tag.get()` now typed as
     `str | AttributeValueList`. mangakaka is already dead; the rest is
     parser-refactor work. mypy is **non-blocking** in CI until then.
   - nodriver's `cdp/network.py` has the §5.5 non-UTF-8 byte that crashes mypy's
     parser; handled with a `follow_imports = skip` override (no source patch
     needed).
1. **Stop the bleeding** — ✅ **DONE.** Made the heavy imports lazy:
   `undetected_chromedriver`, `selenium`, and `cloudscraper` moved out of
   `utils.py` module top into their respective branches of `get_html_from_url`
   (matching how `nodriver` was already done); upload backends lazy-imported
   inside `__main__.upload()`; and `scraper/uploaders/types.py` now guards the
   `Uploader = Union[...]` alias under `TYPE_CHECKING` (it was the remaining
   eager path pulling in dropbox/pcloud via `__main__`'s `Uploader` import).
   Result: `import scraper.__main__` now pulls in **none** of
   uc/selenium/cloudscraper/dropbox/pcloud. Verified by running the suite in a
   base-only venv (no `upload` extra): **131 non-upload tests pass with
   dropbox/pcloud absent** — before this, the eager import broke collection for
   the whole suite (§3.6/§3.8). No new mypy debt. `requests` kept at module top
   (lightweight, used throughout, not a problem dep). Full Fetcher abstraction
   (§5.2) is deferred to Phase 3 — this phase is just the lazy-import relief.
2. **Domain model** — ✅ **DONE.** Added `scraper/selection.py` with a
   `ChapterId` value object (numeric ordering, preserves raw string for url
   round-trip), `sort_chapter_ids` (the single ordering), and `select_chapters`
   (chapter-number-based selection). Wired into `manga.py` (sorter + builder),
   `__main__.get_volume_values` (tokenize only; ranges resolved later),
   `mangafire.py` (replaced `sorted(key=float)`), and `mangafast.py` (replaced
   the lexicographic `vol <= highest` compare — this also un-dropped chapters
   7/8/9 in the fixture). **Behavior change:** `--volumes` selection is now
   chapter-number based, not list-index based, so `9-12` spans decimals like
   `9.22` and tolerates gaps. **Opaque-slug sites** (mangabuddy:
   `vol-54-chapter-name`, `chapter-3000`) carry no extractable number —
   Problem A — so ordering preserves the site's own order and selection falls
   back to positional (1-based), preserving their pre-existing behaviour rather
   than silently selecting nothing. New tests: `tests/test_selection.py` (incl.
   the opaque-slug fallback) + builder gap/decimal cases in `test_manga.py`;
   updated CLI + mangafast expectations.
3. **Fetcher abstraction** — ✅ **DONE.** Added `scraper/fetchers.py`:
   `FetchResult`, `Fetcher` protocol, lazy-import backends `RequestsFetcher`,
   `CloudscraperFetcher`, `BrowserFetcher` (nodriver, with `get` /
   `fetch_json_in_page` / `capture_xhr`), and a `fetch_soup(url, fetcher=)`
   seam. **All parsers migrated** off `utils.get_html_from_url`, which is now
   **deleted**:
   - MangaFire → `BrowserFetcher` (capture_xhr / fetch_json_in_page).
   - mangareader/mangakaka/manganelo/manganato/mangafast → `fetch_soup`
     (RequestsFetcher); 404 handling preserved via `FetchResult.raise_for_status`.
   - mangabuddy/mangapark → `fetch_soup` for the requests-side chapter list,
     `fetch_soup(url, BrowserFetcher())` for the JS volume page.
   - mangago + the shared search path (`base._scrape_results`) → `BrowserFetcher`.
   Tests retargeted at the `fetch_soup` / `BrowserFetcher` seams.
   **selenium → nodriver consolidation:** the old `"selenium"` browser path is
   gone; browser parsers now use nodriver via `BrowserFetcher`. ⚠️ This is a
   behaviour change on browser code that can't be unit-tested (mocked away);
   needs a live download to confirm nodriver renders these sites correctly.
   **Now-dead deps:** `selenium` and `undetected_chromedriver` are no longer
   referenced anywhere in `scraper/` (only in the root `test.py` scratch file);
   `cloudscraper` is available as a backend but unused by any parser. Pruning
   them from `pyproject.toml` (and deleting `test.py`) is left for the Phase 7
   deps cleanup.
4. **Typed `SearchResult` + registry** — ✅ **DONE.** Typed `SearchResult`
   (above) plus the **source registry**: new `scraper/registry.py` with
   `@register_source("name")` decorating each of the 9 site parsers,
   `get_source` / `available_sources` / `load_sources`. `__main__` lost its
   9 explicit parser imports and the hand-maintained `get_manga_parser` dict +
   `--source` choices — both now read from the registry. `parsers/types.py`
   collapsed from ~90 lines of `Union[...]` enumerations to base-type aliases
   (`SiteParser = BaseSiteParser`, etc.), so it no longer imports every parser.
   Adding a site is now: write the parser, `@register_source`, add one line to
   `registry._SOURCE_MODULES`. (Minor: collapsing the unions to the abstract
   base surfaces 2 advisory mypy `call-arg` notes on `parser()` construction in
   menu/download — base-vs-subclass `__init__` typing, non-blocking.)
5. **Smarter probe + "add a source" docs** — ✅ **DONE.** Generalized the
   MangaFire-specific `mangafire_probe.py` into `scraper/probe.py` (`python -m
   scraper.probe <url>`): drives nodriver, logs every ajax/API URL, and a pure
   `analyze_html` surfaces candidate selectors (chapter links, `data-number` /
   `data-src` elements, the largest `<img>` cluster), dumping
   `page.html` / `ajax_log.txt` / `candidates.txt` into
   `tests/test_files/<site>/`. Deleted the old `mangafire_probe.py`. Wrote
   `docs/adding-a-source.md` (the §4.3 checklist, grounded in the real
   building blocks: `fetchers`, `selection`, `SearchResult`, `@register_source`).
   `analyze_html` unit-tested + verified against a real fixture (surfaced 81
   chapter links from the mangakaka page).
6. **Engine parsers** — ✅ **DONE.** Extracted the shared "kakalot-family"
   engine (`scraper/parsers/kakalot.py`): `KakalotMangaParser` /
   `KakalotSearchParser` hold the shared `<li class="a-h">` / `container-
   chapter-reader` / `search/story/` scraping, parameterized by class attrs
   (URL templates, `page_img_attr`, `chapter_href_sep`, result-card selector).
   manganelo/manganato/mangakaka collapsed from ~160 lines each to ~45-line
   parameter subclasses (manganato overrides `_slug`/`_chapters` for its
   layout). Net −118 lines. **Bonus:** the engine's `_extract_number` operates
   on the href string, fixing the same latent `.text`-on-a-str bug that
   manganelo/manganato carried (they had no tests to catch it). New
   `tests/test_kakalot.py` pins each site's URLs/params (valuable — manganelo/
   manganato had no other tests); mangakaka mocks retargeted to the engine
   module. mypy 33 → 29 (deduped union-attr sites). Adding another kakalot-style
   site is now ~a dozen lines of parameters (§4.2 multiplier).
7. **Dead-code removal; CI + test tiering** — ✅ **DONE.** Deps prune (above)
   plus the test cleanup: the `test_page_data` tests now mock
   `base.request_session` correctly (they had mocked the wrong target and fell
   through to real network — the source of the "17-min mangafast" hang); the
   mangakaka `test_all_volume_ids` bs4 breakage was a wrong-argument bug
   (`_extract_number` called `.text` on an href string) and is fixed. Result:
   **`uv run pytest` is green with no flags** (166 pass) — the
   `-k`/`--deselect`/`--ignore` incantation is gone from CI. Registered an
   `integration` marker for future browser/network tests, though the suite is
   currently fully mocked so none are tagged yet. **ruff consolidation done**:
   replaced `pyflakes` + `flake8` + `black` + `isort` (+ `autoflake`) with a
   single `ruff` (lint `E`/`F`/`I`/`W` + formatter), config in `[tool.ruff]`;
   deleted `.flake8`; updated CI, pre-commit, and README. ruff caught a real
   latent bug the old stack missed: a duplicate `"volumes"` dict key in
   `__main__.change_args_to_search` (F601). Lockfile 63 → 55 packages.

---

## 7. Reference: MangaFire as the worked example

`scraper/parsers/mangafire.py` (this branch) already demonstrates the target
patterns for a JSON/ajax site:
- chapter list via `/ajax/manga/<id>/chapter/en` (id = slug after last `.`)
- page list via intercepting `ajax/read/chapter` then re-fetching in-page
- images via curl_cffi (Chrome impersonation) + browser cookies + Referer
- descramble ported from the keiyoushi Tachiyomi extension's ImageInterceptor
- search via the vrf-gated `ajax/manga/search` (type into the live box,
  intercept the call)
- tests parse real captured responses in `tests/test_files/mangafire/`

Note even here the chapter-id wart shows: `all_volume_ids` sorts by `float(v)`
while the rest of the tree uses `natural_sort` or string compares — the §3.1
fix would unify these.
