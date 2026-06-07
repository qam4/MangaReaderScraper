# Backlog — MangaReaderScraper

Single source of truth. Tick items as done. Rationale/evidence for each lives in
`audit.md`. Principle: when we refactor, do it ALL THE WAY (no half-tiers).

Tags: [QUICK] safe/local · [STRUCT] design-first · [PROBE] needs live re-probe.
Each item has a done-when so "done" is unambiguous.

---

## Wave A — quick wins (safe, local, no live deps)

- [x] **A1 [QUICK] Consolidate logging + RichHandler + `--log-level`**
  - DONE: `configure_logging(level)` in utils.py (RichHandler, force=True, reads
    `MANGASCRAPER_LOG_LEVEL` when no arg); removed the 3 basicConfig copies;
    `--log-level` arg; cli() sets env + applies; pools use
    `Pool(initializer=configure_logging)`; `change_args_to_search` skips
    log_level (M1 brittleness surfaced + handled). CAVEAT: rich+tqdm interleave
    in a real multiprocess download NOT verified (mocked suite) — eyeball live.

- [x] **A2 [QUICK] Fix stale default source** (mangareader.net is dead)
  - DONE: `utils.create_base_config` default → `mangabuddy`; `test_utils` assertion
    updated. NOTE: README still shows `source = mangareader` — fix in a docs sweep.
    conftest/test_cli `mangareader` left as-is (they test the default-flows-through
    mechanism, not the product default; mangareader is still a registered source).

- [x] **A3 [QUICK] Strip debug spew + dead code from parsers**
  - DONE: removed raw-soup/object `logger.info/debug(f"...")` dumps from
    mangago.py + mangapark.py (kept intent logs: Manga url / search_url);
    deleted the commented-out `all_volume_ids` block in mangago; removed the
    `# no multi-thread version:` block (manga.py) + commented `from_list`
    (menu.py).

- [x] **A4 [QUICK] Cache `settings()`**
  - DONE: ini parsed once via `_read_settings(path)` (`lru_cache`, keyed on the
    config PATH so test `Path.home`-patching gets a distinct entry, not the
    real-home parse); `create_base_config` calls `cache_clear()` on rewrite.
    CAVEAT: won't pick up an EXTERNAL mid-process edit of the ini until restart
    (fine for a CLI that reads config once).

- [x] **A5 [QUICK] Fix `Volume.total_pages()`** (H2)
  - DONE: now `len(self._pages)` (a count, not `max(page number)`); added
    regression test with a page-number gap (pages 1 & 5 → count 2).

## Wave B — orchestration rebuild (STRUCT; B1+B2 are ONE surface — design together)

- [ ] **B1 [STRUCT] Decouple resolve→download; kill `change_args_to_search`** (M1)
  - WHERE: `__main__.cli()` recursion + `change_args_to_search` + `manga_search`;
    collapse `Menu`/`SearchMenu` (menu.py) into "render table → pick row → return
    SearchResult" (drop unused parent/Back menu-tree machinery).
  - DONE-WHEN: no argv re-serialization/recursion; not-found→search is a plain
    branch; `test_cli.py` updated (READ FIRST — pins current behavior).

- [x] **B2 [STRUCT] Multiprocess boundary returns data, not disk-roundtrip** (H1)
  - WHERE: `manga.py` `MangaBuilder._get_volume_data`/`_get_volumes_data` — child
    procs mutate their own `self.manga` copy (discarded); parent re-derives from
    disk; in-memory `Manga.pages` empty in prod (tests pass only single-threaded).
  - CONFIRMED EMPIRICALLY (this session): on Windows **spawn**, a real-`Pool`
    download leaves the parent `manga.volumes_dict[id]._pages` EMPTY (0 pages) —
    image data lives only on disk. The parent's Manga model is a lie after a real
    run; downstream (upload/remove/bundle) survives only because it's all
    file-path based.
  - ROOT OF WHY TESTS MISS IT: `tests/conftest.py::mocked_pool_imap` (session
    autouse) patches `scraper.manga.Pool.imap` → plain `map`, so the WHOLE suite
    runs single-process. Child mutations stick → builder tests assert
    `volume.page[1]` exists and pass. The suite is structurally blind to the
    multiprocess data flow BY DESIGN of that fixture. (This fixture is part of
    the problem — a real-pool test must opt out of it.)
  - DONE (honest fix): `_get_volume_data` now returns `(volume_id, pages_data)`
    (was `(volume_id, None)`); typed `Optional[VolumeData]`. `get_manga_volumes`
    builds a `pages_by_volume` dict from the worker RETURN values and populates
    each volume's `.pages` from it — so the parent's Manga is correct after a real
    spawn Pool run, not just single-threaded. Two characterization tests added
    (`test_builder_assembles_pages_from_worker_return_value`,
    `..._skips_pages_for_volumes_worker_returned_none`) that stub `_get_volumes_data`
    to assert the parent-side assembly without spawn flakiness. Gates green.
  - DEFERRED to B2-redesign (below): a test exercising the REAL pool (opting out of
    mocked_pool_imap), and the question of retiring mocked_pool_imap entirely.

- [ ] **B3 [STRUCT] Split format writers out of `MangaBuilder`** (god-module A5)
  - WHERE: move `_to_pdf`/`_to_cbz`/`_get_save_method` to e.g. `writers.py`,
    injected into the builder; `manga.py` keeps model + orchestration only.
  - DONE-WHEN: writers independently testable; builder depends on an interface.

- [x] **B4 [STRUCT] Remove `sys.exit()` from parser layer** (L5)
  - WHERE: `parsers/base.py` `BaseSearchParser._scrape_results`.
  - DONE: added `NoSearchResultsFound` domain exception (exceptions.py); the
    parser now raises it instead of `sys.exit()` (removed the `import sys` there);
    `cli_entry()` catches it, logs the message, and `sys.exit(1)` — so process
    termination is the CLI's job, not the parser's. `cli()` itself stays
    exception-raising (testable). Updated the three parser tests
    (mangafast/mangareader/mangakaka `..._with_invalid_query`) to expect
    `NoSearchResultsFound` instead of `SystemExit` (they were also asserting
    inside the raises block, i.e. dead asserts — now assert the message via
    `match=`). Gates green, 318 pass.

- [ ] **B2-redesign [STRUCT] Proper multiprocess boundary + retire the mask** (follow-up to B2)
  - WHY: B2 was an honest minimal fix (parent assembles from worker returns). The
    deeper problems remain: (a) workers still mutate a throwaway `self.manga` and
    redo `add_volume` + disk save inside the child, while the parent ALSO does
    add_volume — duplicated, split-brain ownership; (b) settings/config aren't
    cleanly propagated to spawned workers (worker re-reads ini via initializer);
    (c) `mocked_pool_imap` (session autouse in conftest) still forces the whole
    suite single-process, hiding the real spawn data flow.
  - STEPS:
    1. Make the worker a PURE function: download pages → return
       `(volume_id, pages_data, complete)`; do NOT touch `self.manga` or save to
       disk inside the child. Parent owns Manga assembly AND disk writing (or a
       dedicated writer — see B3).
    2. Pass everything the worker needs explicitly (urls/config), so it doesn't
       depend on parent mutable state surviving spawn.
    3. Add ONE real-pool characterization test that opts out of `mocked_pool_imap`
       (e.g. an opt-out marker/fixture) and asserts parent `.pages` populated.
    4. Decide the fate of `mocked_pool_imap`: scope it to the few tests that truly
       need single-process determinism instead of session-autouse, OR remove it.
  - DONE-WHEN: worker is side-effect-free w.r.t. parent state; a real-pool test
    proves parent Manga is correct under spawn; the global mask is gone or scoped.

## Wave C — finish the parser refactor (PROBE-gated; user runs live probes)

- [ ] **C1 [PROBE] Re-probe MangaFire** — verify vrf/scramble/endpoints still hold
  - Commands (live, headful browser; user runs). Slug: `ad-astra-scipio-and-hanniball.lww3`:
    - `python -m scraper.probe https://mangafire.to/read/ad-astra-scipio-and-hanniball.lww3/en/chapter-1`
    - `python -m scraper.probe https://mangafire.to/manga/ad-astra-scipio-and-hanniball.lww3`
    - `python -m scraper.probe https://mangafire.to/home --search "ad astra"`
  - Inspect probe_out/mangafire/: recommendation.txt, api_backends.txt (can
    curl_cffi reach the no-vrf chapter list?), ajax_log/api_*.json shapes.
  - DONE-WHEN: know if shapes hold → decide cleanup vs rewrite; record findings.

- [ ] **C2 [PROBE] Re-probe + re-derive mangago / mangapark**
  - They scrape Qwik build-hash selectors (`q:key="zn_2"`, `"8t_8"`) + have
    cloudflare-403 notes → likely already broken. Probe each; if still HTML-image
    sites, push onto a shared "browser-HTML" base (like kakalot) or regenerate via
    the scaffold (html image mode exists now).
  - DONE-WHEN: each is working+on a shared base, or removed if the site is gone.
    No duplicated bespoke skeleton remains.

- [ ] **C3 [QUICK, after C1] De-dup `page_data` download loop** (L1)
  - WHERE: identical curl_cffi+Referer+retry loop in `mangabuddy.py`,
    `mangafire.py`, and emitted by `scaffold.py`. Lift to a shared base/helper;
    descramble stays a hook on top.
  - DONE-WHEN: one impl reused by both + scaffold. (After C1 → verified baseline.)

## Wave D — identity / cosmetic (low value alone; fold rename into B if rebuilding)

- [ ] **D1 [QUICK] Rename `SearchResult.chapters` → `chapters_hint`** (+docstring
  "display-only; format varies by site"). NOT `latest_chapter` (meaning varies).
  Only reader is `menu.py`. Update parser construction sites + fixtures.

- [ ] **D2 [QUICK] `menu.py table()` unicode + magic number** — stop
  `.encode("ascii", errors="ignore")` (mangles JP/accented titles); render
  unicode; extract the 70-char truncation to a named constant.

- [ ] **D3 [DECISION] Project rename** — `MangaReaderScraper` names a dead site.
  Package (`scraper/`) already neutral; only project/repo/dist/pyproject name +
  README stale. Friction: repo rename breaks clone URLs/CI/install. Only worth it
  bundled with the Wave B rebuild; standalone it's just paint (A2 is the real fix).

- [ ] **D4 [QUICK] De-personalize `bundle.py`** — `WRITER_DEFAULT = "Fred
  Marchais"` hardcoded as every user's comic author → make configurable (settings);
  drop dead `multi_process = True` toggle. (Full bundle.py cleanup is a larger
  isolable task.)

---

## Suggested order
A (all) → B (B1+B2 designed together, B2 test first) → C (probe-gated) →
D (fold D3 into B if rebuilding). Start nibbling at Wave A.

## Done this session (for reference)
- Probe-assisted source authoring spec: all 19 tasks (Phases 1-3 + html image
  mode), committed + pushed + CI green.
- A2 (stale default → mangabuddy) + A5 (total_pages count) — commit 864c363.

---

## Provenance / reconciliation with docs/code-review.md

IMPORTANT: `docs/code-review.md` + `docs/refactoring-plan.md` are a PRIOR review
(written on the `mangafire-parser-rewrite` branch, pre-refactor). Much of it is
already FIXED. It is a historical evidence doc, NOT a live to-do. Below is the
reconciliation (verified against the current tree, not trusted blindly):

ALREADY FIXED (do not re-do):
- kakalot trio collapsed to one engine (review §2 P2) — done.
- registry replaced get_manga_parser dict + argparse choices + type Unions
  (review §5 P2) — done.
- ChapterId / typed SearchResult (review §2 P3, §5) — done.
- lazy upload imports + pyproject extras; uc removed (review §3 P1, §6) — done.
- test_parsers mocks at the curl_cffi layer now, NOT scraper.utils.requests.get
  (review §1 P1 "wrong layer") — STALE, already fixed.
- base.page_data target fixed (helpers.mocked_request_session) — done.

STILL OPEN — folded into the waves above or added here:
- [ ] **R1 [STRUCT][P1] Two-tier test split (engine vs parser).** The smell:
  generic base-class behavior is tested THROUGH three dead site parsers
  (`tests/helpers.py` `ALL_PARSERS = [MangaReader, MangaKaka, MangaFast]`,
  driven by `test_parsers.py`). Test for which tier a test belongs to: "if the
  site died tomorrow, should the test still mean something?"
  - ENGINE tests (yes → site-agnostic): base-class/orchestration behavior
    (ChapterId, select_chapters, MangaBuilder flow, "no manga set" error, menu,
    fetcher ladder, registry). Test ONCE through a single fake parser
    (`MockedSiteParser` already exists in helpers.py) — NOT through a list of
    real sites. Drop `ALL_PARSERS`/`ALL_SCRAPERS` real-site parametrization for
    these.
  - PARSER tests (no → legitimately site-specific): does THIS site's parser
    extract THIS site's captured fixture correctly. One fixture-backed test per
    SUPPORTED site (what the scaffold generates). Today only mangabuddy/mangafire
    have these; the others don't.
  - DONE-WHEN: engine tests run against a fake parser (no dead real sites in the
    parametrize lists); each live parser has a fixture-backed extraction test;
    green suite means something about the sites actually used.
  - NOTE: user agrees with this framing. NOT "add live sites to ALL_PARSERS" —
    that was the wrong original wording. STRUCT/larger; sequence with the Wave C
    parser work (probe → fixtures → tests) since the fixtures come from probing.
  - SOURCE OF FIXTURES: the probe already writes real responses to
    probe_out/<site>/ and the workflow promotes a curated subset to
    tests/test_files/<site>/. Those ARE the parser-test fixtures, and scaffold's
    generate_tests already emits the matching fixture-backed test. So each Wave C
    re-probe (C1 mangafire, C2 mangago/mangapark) naturally yields its parser
    test — R1's parser tier is a byproduct of probing, not separate work.
- [ ] **R2 [QUICK][P3] `--upload mega` dead choice.** `__main__` argparse
  `choices={"dropbox","mega","pcloud"}` but MegaUploader is commented out →
  advertises a mode that errors. Drop "mega" from choices. Also remove the
  `mega.*` entry in pyproject mypy overrides + commented MegaUploader/Mega mocks
  if we want the sweep complete.
- [ ] **R3 [QUICK][P2] `kcc-c2e` unchecked external binary** — bundle.py already
  RuntimeErrors if missing (improved since review); verify that's sufficient,
  else close. (Likely already adequate — confirm during D4 bundle cleanup.)
- [ ] **R4 [P3] `mypy.ini`/pyproject: `no_strict_optional = True`** masks the
  real None-return bugs (the page_urls Optional issue, B-wave). Revisit tightening
  AFTER B fixes the None returns, else it'll surface many errors at once.
- NOTE: `extract_chapter_number` is NOT orphan (review §3 P3 guessed unused) —
  it has parametrized tests in test_utils.py. Leave it; verify relevance only if
  touching that area.
- README `source = mangareader` (review-adjacent) — folded into A2's note.

Anything in code-review.md NOT listed here is either already fixed or a P3 nit
not worth a backlog slot.
