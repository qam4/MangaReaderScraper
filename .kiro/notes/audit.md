# Honest codebase audit — MangaReaderScraper

Scope: read of scraper/{__main__, manga, download, bundle, menu, utils, selection,
new_types, fetchers, registry, exceptions, parsers/*}. Analysis only — no live run.
Bias check: I'm deliberately listing real problems, but also calling out what is
genuinely good so the "rewrite vs fix" call is honest, not dramatic.

## Verdict up front

This is NOT a "burn it down" codebase. The data/domain layer is good and carries
hard-won site knowledge. The ORCHESTRATION layer (CLI flow + MangaBuilder +
bundle) is where the real debt is, and it's fixable in place. A full rewrite is
not justified by what's here; a targeted rebuild of orchestration MIGHT be, but
even that is optional — most findings are independently fixable.

## Assets (keep — do not rewrite)

- `selection.py` (ChapterId / select_chapters): clean, well-documented, the
  number-vs-index distinction is correct and tested. Best file in the repo.
- `fetchers.py`: the Fetcher protocol + curl_cffi/requests/cloudscraper/browser
  ladder is a sound abstraction; parsers go through it.
- `registry.py`: decorator + module-list registration is clean and extensible.
- `probe.py` / `scaffold.py`: this session's work; pure, tested.
- The parsers encode irreplaceable per-site quirks (descramble, vrf, __NEXT_DATA__).
- 314 passing tests + green CI is a real asset any rewrite forfeits.

## Findings (by severity)

### HIGH — correctness / data-integrity

H1. **`Manga` mutated inside child processes is silently discarded.**
`MangaBuilder._get_volume_data` runs in `Pool(4).imap` (real processes). It calls
`self.manga.add_volume(...)` + sets `.pages` — but those mutations happen in the
CHILD's copy of `self.manga` and are NOT returned to the parent (the function
returns `(volume_id, None)`; pages are dropped). The code even comments
"each volume is created in its own process, so self.manga of the parent is not
changed." The parent then RE-BUILDS volumes from disk afterward
(`get_manga_volumes` re-adds volumes by checking `volume_exists` on disk). So the
in-memory `Manga.pages` is always empty after a real multiprocess run; the data
round-trips through the filesystem instead. Works, but the Manga object is a lie
in the parent (pages live only on disk), and `--upload`/`--remove` then operate on
file paths, not the in-memory model. This is the single biggest "looks like it
does X but actually does Y" trap. Tests don't catch it because they run
single-threaded.

H2. **`Volume.total_pages()` returns `max(self.page)` — the max page NUMBER, not
the count.** `self.page` is a `Dict[int, Page]`; `max()` of it is the largest key.
Mislabeled and would be wrong for any non-contiguous or 0-based page set. Looks
unused in the hot path but it's a latent bug named like a count (echoes the
chapters/count confusion theme).

### MEDIUM — maintainability / structural

M1. **CLI search re-entry** (already in backlog #6): `cli()` serializes parsed
args back to argv via `change_args_to_search` and recurses. Double argparse,
leaky inverse-of-argparse, recursion-out-of-except. Real smell.

M2. **Logging configured 3x identically** (backlog #1): copy-pasted basicConfig;
two are process-pool re-inits. Consolidate + RichHandler + --log-level.

M3. **`bundle.py` is the weakest file.** `create_volume` is ~120 lines doing dir
setup + obsolescence check + cbz assembly + mobi shell-out, all in one method
that ALSO re-inits logging. `multi_process = True` is a hardcoded local (dead
toggle). `WRITER_DEFAULT = "Fred Marchais"` is a personal name hardcoded as the
comic author for everyone. Mixes os.path (here) with pathlib (rest of repo). Heavy
os.walk/extract/rezip with broad `except Exception`. Functional but the least
maintainable code in the project.

M4. **`settings()` re-reads + re-parses the ini file on EVERY call**, and it's
called per-volume-path, per-upload-path, per manga-dir-create. No caching. Also
`create_base_config()` hardcodes the now-dead `mangareader` default (backlog #7).

M5. **`SearchResult.chapters` semantic vagueness** (backlog #2): display-only,
meaning varies per parser.

M6. **`mangabuddy.py` registered as "mangabuddy" but the site is mangak.io.**
Intentional (legacy name), documented — but it's a naming landmine for the next
person, and the default-source/rename discussion (#7/#8) compounds it.

### LOW — polish

L1. `BaseMangaParser.page_data` has a generic impl AND mangabuddy/mangafire
override it with near-identical curl_cffi loops; scaffold emits a 3rd copy
(backlog #4 — de-dup).
L2. Dead/commented code: `Menu.from_list` (commented), several commented imports
in manga.py exceptions, `# no multi-thread version:` blocks left in.
L3. `Volume.__eq__` compares by `str(getattr(...))` over `__dict__` — works but
fragile (stringly-typed equality).
L4. `manga.py` `get_manga_volumes` raises bare `Exception("Empty volumes list")`.
L5. `BaseSearchParser._scrape_results` calls `sys.exit()` on no results — a
library-layer hard exit (should raise; the CLI decides to exit). Tested as
sys.exit, so it's load-bearing behavior now.

## Rewrite vs fix — recommendation

- DO NOT full-rewrite. The asset list above would be re-derived at high risk.
- The defensible "rebuild" is ONLY the orchestration: a clean resolve→download→
  (post: upload/remove/bundle) pipeline that (a) kills change_args_to_search,
  (b) makes the multiprocess boundary return data instead of relying on the
  disk-roundtrip (fixes H1 honestly), (c) centralizes logging + settings caching.
  That's maybe 3 files (__main__, download, manga's builder half) + bundle as a
  separate cleanup.
- Everything else is independently fixable from the backlog without a rewrite.
- Sequencing: backlog #1 (logging) and #7 (stale default) are safe quick wins.
  #6 (CLI re-entry) and H1 (multiprocess data model) are the structural ones and
  should be designed together (they're the same orchestration surface). Bundle
  (M3) is isolable and can be cleaned anytime.

## Honest caveats on this audit

- Static read only; no live run. H1 in particular I inferred from the code +
  the in-tree comments; it should be confirmed by actually running a real
  multi-volume download and inspecting whether Manga.pages is populated in the
  parent (I can't run a browser/network download from here).
- "Works in practice" is true for most of this — the disk round-trip (H1) means
  the product functions even though the in-memory model is misleading. Severity
  is about maintainability/correctness-of-model, not "it's broken for users."
- I have not audited test quality in depth, only that the suite is green and
  mostly mocked.


## Addendum — menu.py (search menu), scrutinized separately

M7. **`Menu` base is built for a nested menu tree that doesn't exist.**
`Menu` has parent/Back machinery (`_add_parent_to_options`, `_add_back_to_choices`)
and `handle_options` is documented to "execute a method from self.options" — i.e.
a navigable menu of menus. But `SearchMenu` always constructs with `parent=None`
and puts `SearchResults` (dict of SearchResult dataclasses) in as options, then
the caller treats the returned item as a dict (`manga["title"]`). So the base
abstraction (menu tree) ≠ how it's used (one-shot result picker). ~60% of Menu is
dead in practice: parent/Back branch never fires, `from_list` is commented out,
`_create_options` is a no-op passthrough. This is the OTHER half of the
disliked search workflow (M1): over-engineered menu base feeding the argv
round-trip. On an orchestration rebuild, collapse Menu+SearchMenu into a simple
"render table → pick row → return SearchResult" function.

L6. **`table()` silently drops non-ASCII titles:**
`metadata["title"].encode("ascii", errors="ignore").decode()` mangles/strips
Japanese/accented manga titles — a real UX papercut for a manga tool. Should
render unicode (tabulate handles it); if a terminal-encoding guard is needed, do
it without destroying the data.

L7. **"Latest Volume" column shows `metadata["chapters"]`** — header disagrees
with the field name, and the value's meaning varies per parser (ties to M5 /
backlog #2). 70-char title truncation is an inline magic number.


## Addendum 2 — architecture & quality pass (remaining parsers + uploaders)

Read: manganato/manganelo/mangakaka (kakalot family), mangago, mangapark,
parsers/types, uploaders/{base,uploaders,types}. Focus: architecture, simplicity,
readability, patterns, smells (NOT tests).

### The big picture: the codebase is HALF-REFACTORED (two tiers)

TIER 1 (refactored, good): kakalot engine, registry, selection, fetchers,
scaffold, probe, parser base/types, uploaders/types. The kakalot family is the
model pattern — manganato/manganelo/mangakaka are ~15 lines each of pure
class-attribute config over KakalotMangaParser/KakalotSearchParser. types.py
modules show real care (Protocol constructor contract; TYPE_CHECKING guard to keep
optional upload deps optional).

TIER 2 (untouched, rough): mangago, mangapark, and the orchestration (manga.py /
__main__ / bundle). Every significant smell concentrates here. Conclusion: the
fix is to FINISH the started refactor, not rewrite.

### Architecture findings

A1. **Proven abstraction left unapplied.** mangago + mangapark are near-identical
in shape (same _scrape_volume 404-check, volume_url, all_volume_ids try/except,
page_urls container->imgs->src) and could share a "browser-rendered HTML" base
like kakalot does — but each re-implements the skeleton. The good pattern exists
and is simply not applied to half the parsers. Biggest architecture smell.

A2. **mangago/mangapark are debug-grade, not production-grade:**
- `logger.info(f"volume_html={volume_html}")` logs an ENTIRE rendered page at
  INFO; multiple `logger.info(f"all_img_tags[0]=...")` raw-object dumps (~15
  across the two files). Leftover print-debugging promoted to logger calls.
- mangago has a large commented-out all_volume_ids block left inline.
- Brittle selectors on Qwik auto-keys: `{"q:key": "zn_2"}`, `{"q:key": "8t_8"}`
  — regenerate on every site rebuild; these parsers are one deploy from breaking
  (exactly what probe/scaffold exists to re-derive).
- `FRED:` inline notes documenting live breakage (cloudflare 403) scattered as
  comments rather than tracked.

A3. **Inconsistent return contract for `page_urls`.** Typed
`-> List[Tuple[int,str]]` but mangago/mangapark/kakalot can `return None` (the
`if volume_html:` else-fallthrough). Real type is Optional; some parsers raise
VolumeDoesntExist instead. Mixed raise-vs-None across parsers; callers defend
only loosely.

A4. **Layer violation: `sys.exit()` in BaseSearchParser._scrape_results.** Domain
code kills the process on no results; that decision belongs to the CLI. (= L5,
restated as architecture.)

A5. **`manga.py` is a god-module (4 responsibilities):** data model
(Page/Volume/Manga) + download orchestration (MangaBuilder + pools) + file-format
writers (_to_pdf/_to_cbz) + disk-path policy. The format writers especially don't
belong in the builder — separate concern bolted on. This is the same surface as
H1 (multiprocess model) and the natural target for the orchestration cleanup.

### Verdict (architecture)

Bones are good (Fetcher ladder, registry, ChapterId, kakalot engine, Base triad).
Problems are: (1) uneven application of the good patterns, (2) one god-module
(manga.py), (3) layer leaks (sys.exit in parser, writers in builder). NOT a
rewrite — FINISH the refactor: push mangago/mangapark onto a shared browser-HTML
base (or regenerate via scaffold), split format-writers out of MangaBuilder, fix
orchestration (H1 + CLI re-entry). The unrefactored half is where all the smell is.

### Caveat
Still static-read only. mangago/mangapark are likely BROKEN live already (Qwik
keys + the FRED cloudflare notes) — a probe would confirm; don't refactor them
blind, re-derive them.
