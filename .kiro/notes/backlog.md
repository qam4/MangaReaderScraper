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

- [x] **B1 [STRUCT] Decouple resolve→download; kill `change_args_to_search`** (M1)
  - WHERE: `__main__.cli()` recursion + `change_args_to_search` + `manga_search`;
    collapse `Menu`/`SearchMenu` (menu.py) into "render table → pick row → return
    SearchResult" (drop unused parent/Back menu-tree machinery).
  - DONE (part 1, CLI): removed `change_args_to_search` and the `cli()` recursion.
    On `MangaDoesNotExist` the not-found→search fallback is now a plain in-line
    branch (set `args["search"]`, call `manga_search`, normalize volumes, download
    once) — no argv re-serialization, no re-entry. Extracted `normalize_volumes()`
    so the volume-token flattening is shared by the direct and fallback paths.
  - DONE (part 2, menu): collapsed `menu.py` to a single flat `SearchMenu` (render
    table → prompt → return chosen SearchResult). Deleted the `Menu` base class and
    its parent/child/Back tree machinery (production never used a parent — only the
    synthetic test fixtures did). Extracted the 70-char truncation to
    `TITLE_MAX_WIDTH` (also closes part of D2). Updated tests: dropped the
    `menu`/`menu_no_choices` conftest fixtures + `test_parent_menu`/`test_back_button`;
    `test_menu.py` now covers `handle_options` (valid selection + invalid choice)
    through `SearchMenu` with `MockedSearch`. `test_cli.py` unchanged and green —
    the search-fallback test now exercises the plain branch. Gates green, 321 pass.

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

- [x] **B3 [STRUCT] Split format writers out of `MangaBuilder`** (god-module A5)
  - WHERE: move `_to_pdf`/`_to_cbz`/`_get_save_method` to e.g. `writers.py`,
    injected into the builder; `manga.py` keeps model + orchestration only.
  - DONE: new `scraper/writers.py` with a `VolumeWriter` Protocol + `PdfWriter`/
    `CbzWriter` + `get_writer(filetype)` factory. `MangaBuilder.__init__` now
    holds `self.writer = get_writer(filetype)` and `_get_volume_data` calls
    `self.writer.write(volume)` instead of the in-class save methods. Removed
    `_to_pdf`/`_to_cbz`/`_get_save_method` and the now-unused imports (zipfile,
    tempfile, BytesIO, PIL.Image, ImageReader, canvas, Callable) from manga.py.
    Writers duck-type Volume (TYPE_CHECKING import) so no circular import. Added
    `tests/test_writers.py` (5 tests: factory, pdf/cbz signatures, cbz naming
    schema + order, empty-volume no-op) — writers are now independently testable.
    Gates green, 323 pass.

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

- [x] **C1 [PROBE] Re-probe MangaFire** — verify vrf/scramble/endpoints still hold
  - CAPTURED (user, live headful, 3x `--site mangafire/{chapters,images,search}`).
  - FINDINGS (analysis of the captures):
    * **images — HOLDS.** `images/api_08_*.json` = `result.images` array of
      `[url, ?, offset]` triples, exactly what `page_urls` reads (`entry[0]` url,
      `entry[2]` offset). Image call still vrf-gated
      (`/ajax/read/chapter/<id>?vrf=...`) → browser `capture_xhr` still required.
      CAVEAT: every offset was 0 here (this manga isn't scrambled now), so the
      `descramble()` path was NOT exercised — shape intact, descramble unverified.
    * **chapters — shape HOLDS, parser endpoint NOT exercised (gap).**
      `images/api_06_en.json` confirms the `<li><a data-number=.. data-id=..>`
      shape (decimals present: 28.22/21.22/9.22 → ChapterId decimal handling
      matters & works). BUT the parser calls `/ajax/manga/<id>/chapter/en`
      (no vrf) and the probe never hit it — the `/manga/` page server-renders the
      chapter <li>s, and the reader page uses a DIFFERENT endpoint
      `/ajax/read/<id>/chapter/en?vrf=...`. So the parser's specific no-vrf
      endpoint is unconfirmed by this probe.
    * **search — INCONCLUSIVE.** Search action hit a Cloudflare Turnstile wall;
      no `ajax/manga/search` captured (recommendation: "no search endpoint seen").
    * curl_cffi clears CF (200) where plain requests gets 403 — consistent with
      the parser; but api_backends only tested manifest/panel/reading-get (cap 3,
      vrf data endpoints not among them).
  - DECISION: **cleanup, not rewrite.** Core assumptions hold. Follow-ups → C8/C9.
  - REALITY CHECK (user, after analysis): the existing MangaFire parser WORKS
    end-to-end RIGHT NOW with no manual captcha. That reframes the findings:
    * The probe's "CHALLENGE WALL DETECTED" (turnstile / challenge-platform
      markers) is a **FALSE POSITIVE** here — the browser auto-cleared it and the
      live parser reaches every data path. The detector is tripping on Cloudflare
      *infrastructure* markers in the HTML, not an actual block. → C9.
    * The no-vrf chapter-list endpoint `/ajax/manga/<id>/chapter/en` is exercised
      by a working run (all_volume_ids → fetch_json_in_page) → it WORKS. The C1
      probe just never pointed at it (coverage gap, not a parser problem). C6
      dropped.
    * Search: a slug download (`--manga <slug>`) doesn't exercise search, so
      "it works" may not cover the search path — C7 stays as a (low-pri) confirm.

- [ ] **C7 [PROBE, LOW-PRI] Confirm MangaFire search path** — the C1 search run
  tripped the probe's (false-positive) challenge detector and captured no
  `ajax/manga/search`. A slug download doesn't exercise search, so it's the one
  stage not yet confirmed working. Validate via the parser's own `capture_xhr`
  (the parser sets `i.value` + dispatches input/keyup, unlike the probe's
  send_keys+Enter) or a `--search` re-probe.
  - DONE-WHEN: `ajax/manga/search` shape confirmed (parser parses `a.unit` cards
    → `/manga/<slug>` + `Chap N`), or a live search via the parser returns hits.

- [ ] **C8 [QUICK, LOW-PRI] Verify MangaFire descramble on a scrambled chapter** —
  the C1 image capture had offset 0 on every page (no scramble), so `descramble()`
  was not exercised. If you happen onto a chapter with offset > 0, confirm it
  round-trips to a valid JPEG; else this is just unverified, not broken (the
  parser works on non-scrambled chapters today).

- [x] **C9 [QUICK] Probe: challenge detection is too trigger-happy (false positive)**
  - SURFACED by C1: the probe yelled "CHALLENGE WALL DETECTED" on all three
    MangaFire pages purely from Cloudflare infra markers (`turnstile`,
    `/cdn-cgi/challenge-platform`) in otherwise-fine HTML — yet the browser
    cleared CF automatically and the shipped parser reaches every endpoint.
  - DONE: `detect_challenge(html, has_real_content=False)` now suppresses the
    weak CF-infra markers when real content is present. `analyze_html` computes
    the content signal first (chapter_links / data-number / data-src / an img
    cluster > 1) and passes `has_real_content`, so a fully-rendered page is no
    longer flagged as a wall just for carrying CF's always-on script. Strong
    interstitial phrases still always flag; a small CF page with NO real content
    still flags. Added 3 regression tests (incl. the MangaFire shape: small +
    turnstile script + chapter links → NOT a challenge). Gates green, 324 pass.

- [ ] **C4 [QUICK] Probe: auto-namespace output by URL stage + guard overwrites**
  - WHY (surfaced this session): every probe run writes fixed filenames via
    `_write` (plain `write_text`, no clearing); repeated runs into the same
    `probe_out/<host>/` silently clobber recommendation.txt et al. and orphan
    stale `api_*.json`. A tool meant to be run several times per site overwrites
    its headline output with no warning — a footgun (hit during C1).
  - STEPS: (a) derive the out subfolder from the URL PATH, not just the host
    (`/manga/` → chapters, `/read/`|`chapter` → images, a `--search` run →
    search), so the 3 stage runs land in distinct folders WITHOUT needing
    `--site`; (b) when about to write into a non-empty out_dir, warn (or require
    `--fresh`/`--force`) before overwriting an existing recommendation.txt.
  - DONE-WHEN: 3 stage probes of one site no longer collide by default; a
    re-run into a populated dir is either namespaced or explicitly confirmed.
    Offline-testable (path→subfolder mapping is pure). Low risk.

- [ ] **C5 [STRUCT][NORTH STAR] Single-entry multi-stage probe** — `probe <manga-url>
  [--search "term"]` captures ALL stages in one run
  - VISION (user's original mental model): point the probe at the manga page and
    have it probe everything — capture the series page (chapters), AUTO-FOLLOW the
    first detected chapter link to capture the reader page (images), and run the
    search action — writing each stage to its own subfolder and synthesizing ONE
    combined recommendation.txt across all three.
  - FEASIBILITY (the parts already exist, this is mostly orchestration):
    * chapters→images IS a real link: `analyze_html` already extracts
      `chapter_links` from the manga page → follow the first href, second capture
      pass. Doable.
    * search is NOT derivable from the manga URL — it needs a query. `--search`
      already opens the site, finds the search box, types, and triggers the
      search. So the irreducible input is the search TERM; `probe <manga-url>
      --search "term"` is the realistic "one command" ceiling.
  - CAVEATS (why it's STRUCT, not QUICK): adds browser navigation/wait
    orchestration; the chapter-link-follow and search-box-find are the fragile
    site-specific bits; live-only to validate; pushes against the probe's current
    "capture exactly the page you point me at" ethos. Sequence deliberately, not
    mid-C1. Directly serves the "adding a parser was a huge burden" complaint.
  - DONE-WHEN: a single `probe <manga-url> --search "term"` yields per-stage
    captures + one combined recommendation, with the multi-page navigation
    covered by tests (mock the browser/nav seam) where possible.

- [ ] **C2 [PROBE] Re-probe + re-derive mangago / mangapark**
  - They scrape Qwik build-hash selectors (`q:key="zn_2"`, `"8t_8"`) + have
    cloudflare-403 notes → likely already broken. Probe each; if still HTML-image
    sites, push onto a shared "browser-HTML" base (like kakalot) or regenerate via
    the scaffold (html image mode exists now).
  - DONE-WHEN: each is working+on a shared base, or removed if the site is gone.
    No duplicated bespoke skeleton remains.

- [x] **C3 [QUICK, after C1] De-dup `page_data` download loop** (L1) — THE main
  answer to "MangaFire has a lot of ad-hoc code"
  - COMPARISON FINDINGS (mangafire.py vs mangabuddy.py, read side by side):
    Two parsers differ for two reasons; only one is reducible.
    * IRREDUCIBLE (site-shape, keep as-is): mangak.io has an OPEN JSON API
      (curl_cffi hits `api.mangak.io` directly); MangaFire's data calls are
      vrf-gated by obfuscated JS so it MUST drive a browser (capture_xhr /
      fetch_json_in_page). And descramble (slice-shuffle) is MangaFire-only.
      These are not cruft — the site fights harder.
    * REDUCIBLE (MangaFire predates the tooling): the image-download path. The
      `curl_cffi.Session(impersonate="chrome")` + Referer + 5-try retry loop was
      copy-pasted in `mangafire.page_data`, `mangabuddy.page_data`, AND emitted by
      `scaffold.py` — THREE copies.
  - DONE: added `download_image(url, headers, cookies, max_tries, timeout,
    impersonate, label)` to `fetchers.py` — the single curl_cffi+Chrome+retry
    download loop, returning `bytes | None` (callers own placeholder-on-fail,
    validation, descramble). `mangabuddy.page_data` is now a thin call to it;
    `mangafire.page_data` calls it (passing harvested `cookies`) then applies
    `descramble` as a post-download HOOK; the scaffold's `_PAGE_DATA_METHOD`
    emits a `download_image(...)` call instead of an inline loop (+ imports it).
    Removed all three bespoke loops + the direct `from curl_cffi import requests`
    in both parsers. Added 4 `download_image` unit tests (success, retry-then-
    success, exhausted→None, cookies propagation); updated the scaffold import-line
    assertion. Gates green, 328 pass.
  - VERDICT on the original question: MangaFire needed NO rewrite. vrf + descramble
    are irreducibly site-specific; the only genuine "ad-hoc" debt was the
    duplicated download loop — now one shared helper, descramble a hook on top.

## Wave D — identity / cosmetic (low value alone; fold rename into B if rebuilding)

- [ ] **D1 [QUICK] Rename `SearchResult.chapters` → `chapters_hint`** (+docstring
  "display-only; format varies by site"). NOT `latest_chapter` (meaning varies).
  Only reader is `menu.py`. Update parser construction sites + fixtures.

- [ ] **D2 [QUICK] `menu.py table()` unicode + magic number** — stop
  `.encode("ascii", errors="ignore")` (mangles JP/accented titles); render
  unicode; extract the 70-char truncation to a named constant.
  - PARTIAL (B1 part 2): the 70-char truncation is now the `TITLE_MAX_WIDTH`
    constant. STILL OPEN: the `.encode("ascii", errors="ignore")` that drops
    non-ASCII title characters — render unicode instead.

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
