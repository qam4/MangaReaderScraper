# MangaReaderScraper — Code Review

> **Historical snapshot (pre-refactor).** This describes the `scraper/` package
> as it was on the `mangafire-parser-rewrite` branch, before the Wave A–F
> cleanup. Many issues here are now fixed (e.g. `--volumes` → `--chapters`,
> dead sources retired, the fetcher abstraction). Kept as the evidence the
> refactor was based on; not a description of the current tree.

Status: review note. A read-through of the whole `scraper/` package and the
test suite on the `mangafire-parser-rewrite` branch, written to surface
concrete problems and improvement directions. Pairs with
`docs/refactoring-plan.md` (which proposes the fixes); this doc is the
evidence.

Context: this is a personal "make it work once, scrape a manga, move on" tool.
The review is calibrated to that — it flags what actually bites when you come
back to add a site months later, not style nits.

Severity tags: **[P1]** actively misleading/broken, **[P2]** real maintenance
cost, **[P3]** polish.

---

## 1. Tests (the most important finding)

### [P1] The test suite exercises the DEAD sites and ignores the LIVE ones

`tests/helpers.py`:

```python
ALL_SCRAPERS = [MangaReader, MangaKaka, MangaFast]
ALL_PARSERS  = [MangaReaderMangaParser, MangaKakaMangaParser, MangaFastMangaParser]
```

All three are marked `# dead` or cloudflare-broken in `__main__.py`. Every
parametrized test in `test_parsers.py` runs over **only** these. The six sites
you actually use — mangafire, mangabuddy, mangago, mangapark, manganato,
manganelo — are not in `ALL_PARSERS` at all. So the parser test suite proves
the broken sites behave and says nothing about the working ones.

### [P1] `test_404_errors` / `test_non_404_errors` mock the wrong layer

```python
@mock.patch("scraper.utils.requests.get")
def test_404_errors(mock_request, mangaparser): ...
```

This patches `requests.get`. But live parsers fetch via selenium / nodriver /
cloudscraper, which never touch `requests.get` and never raise
`requests.exceptions.HTTPError`. The test only "works" because it's restricted
to the three dead `requests`-based parsers (see above). For everything else the
404/403 handling is **dead code that no test could exercise** — and indeed
several parsers catch `requests.exceptions.HTTPError` around a selenium call
that can't raise it.

### [P1] CLI tests hard-code a dead default source

`test_cli.py` `PARAMETERS` and `SEARCH_PARAMETERS` all assert
`"source": "mangareader"`. The suite's correctness is pinned to a dead site
being the default config. Change the default and the CLI tests break for
reasons unrelated to the CLI.

### [P2] No fixtures for the live sites

`tests/test_files/` has html fixtures for mangareader/mangakaka/mangafast only.
The MangaFire rewrite added the first real fixtures for a live site
(`tests/test_files/mangafire/*.json`). That pattern — capture real responses,
parse them in tests — should be the standard; right now it's the exception.

### [P2] Unit tests can't run without a browser stack

Covered in the plan (§3.8): `conftest` → `manga` → `parsers.types` → every
parser → `base` → `utils` → top-level `undetected_chromedriver`. A pure parsing
unit test transitively needs reportlab, dropbox, lxml, and a chromedriver shim.
`test_mangafast` triggers a *real* selenium run (~17 min observed). There's no
unit/integration split.

### [P3] `pytest.ini` measures coverage but enforces nothing

`addopts=--cov scraper` with no `--cov-fail-under`. Coverage is reported and
ignored. Given §1, the number is also misleading (it counts dead-path tests).

---

## 2. Parsers — massive duplication, one real engine per cluster

### [P2] Three parsers are the same engine

`mangakaka.py`, `manganelo.py`, `manganato.py` are near-identical:
- chapter pages parsed from `div.container-chapter-reader` → `img`
- chapter list from `li.a-h`
- the `"404 NOT FOUND"` string check
- the dead `requests.exceptions.HTTPError` catch
- the same `_extract_number` split-on-`-`/`_` shape

They differ only in `base_url` and URL templates (`/chapter/<m>/chapter_<v>` vs
`/chapter/manga-<m>/chapter-<v>` vs `/manga-<m>/chapter-<v>`) and `src` vs
`data-src`. This is one engine (the MangaKakalot/Manganelo family) wearing three
files. `mangabuddy` is a close cousin. The classes even *say so* in their
docstrings: "Seems to be the same as ... Can probably use this class for ...".

This is the strongest argument for **engine-based parsers** (plan §4.2): these
three collapse to one engine class + three `base_url`/template configs.

### [P2] `page_urls` violates its own contract

Base declares `page_urls(self, volume) -> List[Tuple[int, str]]`. Most parsers
`return None` when `_scrape_volume` yields nothing. Callers in `manga.py`
iterate the result; a `None` is a latent `TypeError`. Pick one: raise
`VolumeDoesntExist`, or return `[]`. Never `None`.

### [P2] Dead error handling copied everywhere

Every `_scrape_volume` wraps a selenium/nodriver fetch in
`except requests.exceptions.HTTPError`. Those backends don't raise it, so the
404 branch is unreachable. The real "not found" signal is the `"404 NOT FOUND"`
text check — which is itself fragile (depends on the site literally rendering
that string).

### [P3] `_extract_number` type lies

In `mangabuddy`/`mangapark`/`mangago`, `_extract_number(self, vol_tag: Tag)` is
annotated `Tag` but is actually called with a `str` (an href) and does
`.split("/")`. The annotation is wrong; mypy can't help because of the
`# type: ignore`s sprinkled around.

### [P3] Search dict is hand-built and weakly typed

Every search builds `{"title","manga_url","chapters","source"}` by hand.
`chapters` is a string in most parsers but `0` (int) in
manganato/mangapark/mangago when no chapter is found — yet `SearchResults` is
`Dict[str, Dict[str, str]]`. The menu later does
`metadata["chapters"]` assuming a string. (Plan §3.4/§5.3: typed
`SearchResult`.)

### [P3] Logging level abuse

`mangago.py` logs entire page HTML at `logger.info` (e.g.
`logger.info(f"volume_html={volume_html}")`), and uses `info` for
per-element debug detail. Most other parsers use `debug` for the same thing.
Noisy and inconsistent.

### [P3] Commented-out code blocks

`mangago.all_volume_ids` carries a large commented-out alternative
implementation; `manganelo`/`mangareader` have commented `[latest]` variants.
Either delete or move to a note. They rot.

---

## 3. `utils.py`

### [P1] Top-level `import undetected_chromedriver` breaks imports

Already in the plan (§3.2): module-top uc import means *anything* importing
`utils` fails on Python ≥3.12 (distutils removed). uc is also superseded by
nodriver for this project.

### [P2] `get_html_from_url` is a 5-way string switch with silent failure

The `else: logging.error("type not supported")` branch falls through and then
`text` is referenced unbound → `UnboundLocalError`. A typo'd backend name
crashes obscurely instead of failing fast.

### [P3] `extract_chapter_number` looks unused / half-related

It parses "chapter" out of a free-text string with its own decimal-stripping
logic — a *fourth* chapter-numbering scheme (see plan §3.1) that doesn't match
what the parsers actually produce. Verify it's used; if not, delete.

---

## 4. `bundle.py`

### [P2] Hard-coded personal value in shared code

`WRITER_DEFAULT = "Fred Marchais"` is baked in and written into every
`ComicInfo.xml`. For a repo that's public/forkable this should be config
(`settings()`), not a constant.

### [P2] External binary dependency, unchecked

`subprocess.run(["kcc-c2e", ...])` assumes KindleComicConverter is installed and
on PATH; no existence check, no error handling on non-zero exit. Silent no-op
MOBI if it's missing.

### [P3] `multi_process = True` hardcoded toggle

A debug switch left in the code. Should be a parameter or removed.

---

## 5. `__main__.py`

### [P2] Source list maintained in two places, plus the unions

The `get_manga_parser` dict and the argparse `choices` set are separate literals
that must stay in sync, on top of the four `Union`s in `types.py`. (Plan §3.3 /
§5.3: registry.)

### [P3] `--upload` still advertises `mega`

`choices={"dropbox","mega","pcloud"}` but mega is commented out in
`uploaders.py`. Advertises a mode that errors.

### [P3] Inconsistent `--volumes` semantics

`get_volume_values` ranges with `range(int(start), int(end)+1)` (int-only) but
single values pass through as strings. So `1-5` yields ints-as-strings while
`9.22` would pass through but never be reachable via a range. (Plan §3.1.)

---

## 6. Packaging / deps / config

### [P2] `requirements.txt` is flat, unpinned, and redundant

Six overlapping HTTP/browser libs (requests, cloudscraper, selenium, uc,
nodriver, curl_cffi), no versions, no dev/runtime split, no `pyproject`
dependency groups. (Plan §3.7 / §5.5.)

### [P3] `mypy.ini` weakened and stale

`no_strict_optional = True` disables a whole class of None-safety checks (and
this codebase has real None-return bugs — §2). Still lists `mega.*` overrides
for a removed module.

### [P3] `.gitignore` blanket-ignores `*.json`

Which is why the MangaFire fixtures needed `git add -f`. As JSON fixtures become
the test standard, this rule fights you. Scope it (e.g. ignore output dirs, not
`tests/test_files/**`).

---

## 7. What's genuinely fine

- The site/manga/search parser split is a good shape.
- `base.page_data` (retry + Pillow validation + placeholder page) is solid.
- The `(num, bytes, status)` flow + ThreadPool/Pool parallelism work.
- `bundle.is_obsolete` (mtime-based rebuild skipping) is a nice touch.

---

## 8. Priority order (if you ever act on this)

Mirrors the plan's phasing, but from a review lens — what's most misleading or
costly first:

1. **[P1] Fix or delete the lying tests.** Either point them at the live sites
   with real fixtures, or stop pretending. Right now green means nothing.
2. **[P1] Lazy imports** (uc, upload) so the suite runs on modern Python.
3. **[P2] One `ChapterId` + one engine class for the kakalot trio.** Biggest
   real-code dedupe and the recurring-bug source.
4. **[P2] `page_urls` contract** (no more `None`), typed `SearchResult`.
5. **[P3] Sanitize**: kill commented blocks, fix log levels, de-personalize
   `bundle.py`, scope `.gitignore`, prune deps.

The theme: the code *works* when you babysit it for one manga, but nothing
*tells the truth* — tests cover dead paths, error handling can't fire, types
lie, and the same engine is copied three times. The high-value work is making
the project honest about itself, starting with the tests.
