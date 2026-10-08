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
    log_level (M1 brittleness surfaced + handled).
    UPDATE: A6 reworked the bar to rich.progress (dropped tqdm), but the bar is
    still NOT pinned under multiprocess logging — see A6/A7.

- [x] **A2 [QUICK] Fix stale default source** (mangareader.net is dead)
  - DONE: `utils.create_base_config` default → `mangabuddy`; `test_utils` assertion
    updated. README config example `source = mangareader` → `mangabuddy` (fixed
    in the docs sweep).
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

- [x] **A6 [QUICK] Pin the progress bar (rich.progress, shared Console)** — PARTIAL
  - WHY (surfaced this session): the download/bundle bars used `tqdm.rich` +
    `logging_redirect_tqdm` ALONGSIDE the A1 `RichHandler`. tqdm.rich has its own
    rich `Live`, and `logging_redirect_tqdm` only knows how to pin CLASSIC tqdm,
    so the bar wasn't kept at the bottom.
  - DONE: added `utils.get_console()` (one shared `rich.console.Console`,
    lru_cache'd). `configure_logging` builds `RichHandler(console=...)` with it,
    and both `MangaBuilder._get_volumes_data` and `Bundle.bundle` render a
    `rich.progress.Progress(console=get_console())` instead of tqdm. Dropped the
    `tqdm` dependency and the dead `multi_process` toggle. Gates green.
  - DID NOT ACTUALLY PIN THE BAR (user confirmed live): the bar still scrolls,
    logs appear everywhere. ROOT CAUSE (diagnosed): the progress bar runs in the
    PARENT, but the per-volume log lines (`adapter.info("Downloading volume..")`,
    "done", etc.) are emitted from the WORKER processes — each worker runs
    `configure_logging` as its Pool initializer and gets its OWN RichHandler/
    Console (get_console is lru_cache'd PER PROCESS). Worker logs write straight
    to the shared stderr fd, bypassing the parent's rich Live region. Rich's
    Live/Progress is single-process by design and CANNOT coordinate a pinned
    region across processes (confirmed: this is a known tqdm/rich limitation; the
    ecosystem fix is a logging queue funneling worker output to one process). So
    A6 fixed only the parent-side console sharing — necessary but insufficient.
  - REMAINING WORK → A7 below.

- [x] **A7 [STRUCT-lite] Actually pin the progress bar across workers** — DONE
  via the threading migration (see "Threading migration" note below). The bar
  could only be pinned if the parent controlled all terminal writes during the
  download; under the old process Pool the workers logged to a separate stderr
  the parent's rich Live couldn't coordinate. Switching the download pool (and
  bundle's pool) from a process `Pool` to a `ThreadPool` means the workers log
  in the SAME process, so the one shared rich Console/Live pins the bar while
  logs scroll above. USER-CONFIRMED LIVE: "bar is pinned". This resolves A6's
  leftover caveat too (A6 fixed only parent-side Console sharing; threads close
  the cross-worker gap).

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

- [x] **B2-redesign [STRUCT] Proper multiprocess boundary + retire the mask** (follow-up to B2)
  - WHY: B2 was an honest minimal fix (parent assembles from worker returns). The
    deeper problems remained: (a) the worker still mutated a throwaway self.manga
    and redid add_volume + disk save inside the child; (b) config didn't propagate
    to spawned workers (worker re-read ini); (c) mocked_pool_imap (session
    autouse) forced the whole suite single-process, hiding the real spawn flow.
  - DONE:
    1. Worker is now HERMETIC: `_download_volume(index, volume_id, already_on_disk)`
       only downloads pages and returns a `VolumeDownload` dataclass — it does NOT
       mutate self.manga, NOT write to disk, NOT read settings(). Replaced the
       `VolumeData` tuple alias with the explicit `VolumeDownload` (volume_id,
       volume_index, pages|None, complete).
    2. Parent owns everything config/disk/state: `_get_volumes_data` computes
       `already_on_disk` (the only settings/disk step) per volume in the PARENT
       and passes it into each worker; `_add_download_to_manga` registers the
       volume, assembles pages from the RETURN value, and writes via the writer.
    3. Real-pool test added: `test_builder_populates_pages_under_real_pool`
       (`@pytest.mark.real_pool`) runs the ACTUAL spawn Pool and asserts the
       parent Manga has pages — green (pre-redesign this showed 0 pages).
    4. mocked_pool_imap retired-as-global: changed from session-autouse to
       function-autouse that respects a `real_pool` opt-out marker (registered in
       pyproject). The mask is now scoped, not forced on the whole suite.
  - Gates green, 335 pass.

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
    * **search — INCONCLUSIVE.** No `ajax/manga/search` captured
      (recommendation: "no search endpoint seen"). CORRECTED later: this was
      NOT a Cloudflare wall. Both captured pages are the normal home page with
      the search box and none of the interstitial phrases; the "challenge
      wall" label was the probe's weak-marker false positive (the always-on
      challenge-platform script). The search request simply never fired.
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

- [x] **C7 [PROBE, LOW-PRI] Confirm MangaFire search path** — the C1 search run
  tripped the probe's (false-positive) challenge detector and captured no
  `ajax/manga/search`. A slug download doesn't exercise search, so it's the one
  stage not yet confirmed working. Validate via the parser's own `capture_xhr`
  (the parser sets `i.value` + dispatches input/keyup, unlike the probe's
  send_keys+Enter) or a `--search` re-probe.
  - DONE-WHEN: `ajax/manga/search` shape confirmed (parser parses `a.unit` cards
    → `/manga/<slug>` + `Chap N`), or a live search via the parser returns hits.
  - HOW (decided with the user): NOT another probe run, which would repeat the
    June failure (it launches its own browser and types keystrokes; the
    request never fired). Run the parser's own search at home:
    `uv run manga-scraper --search naruto --source mangafire`. It uses the
    shared BrowserFetcher session, which since 2026-06-12/13 waits for a manual
    Turnstile click and can keep the clearance via MANGASCRAPER_BROWSER_PROFILE.
    If it fails, rerun with `--log-level DEBUG` and bring the log back.
  - FIRST LIVE RUN (user, at home, 2026-10-07): it loaded mangafire.to/home and
    ran the typing script (`Runtime.evaluate` returned a boolean; its value is
    cut off in the CDP debug log), but no `ajax/manga/search` request followed,
    so the 45 s capture timed out. Still open: WHY the request never fired.
  - That timeout then hung the CLI forever (looked "stuck after starting
    Chrome"). Cause: `_BrowserRuntime.submit` polled with
    `future.result(timeout=0.5)` and `except concurrent.futures.TimeoutError:
    continue`; on Python 3.11+ that class IS the builtin TimeoutError, which the
    op's own `asyncio.wait_for` raises, so EVERY browser-op timeout was taken
    for "still running" and spun forever. Fixed, with a test that hung on the
    old code. Separately, an empty search result printed an empty table and
    waited at the `>>` prompt; the menu now raises NoSearchResultsFound.
  - NEXT: pull and rerun. It should now end with "MangaFire search timed out".
    With `--log-level DEBUG`, the new line "capture_xhr trigger_js on ...
    returned ..." says whether the search box was missing (False) or found but
    typing didn't trigger the site's search (True).
  - SECOND LIVE RUN (user, 2026-10-08): it returned False, so the scraper's
    Chrome found no `input[name=keyword]` 3 s after loading /home. Meanwhile
    the user's own Chrome loads mangafire.to with a search box, and the June
    capture had `<input name="keyword">` inside `.search-inner`. So either the
    scraper's Chrome was on a different page at that moment (a Cloudflare
    check, or not drawn yet), or the box's markup changed. Not yet known which.
  - capture_xhr now stops as soon as the trigger script returns False
    (CaptureTriggerFailed) instead of waiting out its timeout, and the error
    names the page the browser was on: url, title, and whether it looks like a
    Cloudflare check. The MangaFire search reports it as "couldn't find the
    search box (...)". NEXT: pull and rerun; that message decides between the
    two causes above.
  - CLOSED (2026-10-08), superseded by C12: it was the second cause. A fresh
    `--multi` probe (`probe_out/oct/mangafire/`) showed the whole site was
    rebuilt: no `input[name=keyword]`, no `ajax/manga/search`; search is now
    `/api/titles?keyword=...`. The parser was rewritten; confirming the new
    search live is part of C12's DONE-WHEN.

- [x] **C8 [QUICK, LOW-PRI] Verify MangaFire descramble on a scrambled chapter** —
  the C1 image capture had offset 0 on every page (no scramble), so `descramble()`
  was not exercised. If you happen onto a chapter with offset > 0, confirm it
  round-trips to a valid JPEG; else this is just unverified, not broken (the
  parser works on non-scrambled chapters today).
  - CHECKED: the C1 chapter capture (`images/api_08_1488635.json`) lists 54
    pages, every one `[url, 1, 0]`, so offset 0. The field is still sent, so
    either scrambling is gone or only some chapters get it; a re-probe only
    helps if it lands on one. Left as untested-not-broken (agreed with user).
  - CLOSED (2026-10-08), moot: the rebuilt site's page list
    (`/api/chapters/<id>`, October capture) gives each page only `url`,
    `width` and `height`, with no offset field. The descramble code was removed
    in the C12 rewrite. If a downloaded page ever comes out sliced, reopen.

- [ ] **C12 [PROBE] MangaFire rewrite for the rebuilt site (code done, NOT verified live)**
  - FOUND in the October `--multi --search naruto` probe
    (`probe_out/oct/mangafire/`, gitignored): the site was rebuilt and every
    stage of the old parser was broken. Series pages are
    `/title/<hid>-<slug>` (`92kk8-naruto`), reader pages
    `/title/<hid>-<slug>/chapter/<id>`, and the data comes from a JSON API:
    `/api/titles?keyword=...&page=1&limit=30` (search),
    `/api/titles/<hid>` (details, with `authors`),
    `/api/titles/<hid>/chapters?...&page=N&limit=20` (chapter list, 20 a page,
    `meta.hasNext`), `/api/chapters/<id>` (page images). Typing alone fires a
    `limit=5` suggestions call with no `page=`; the home page's own list has no
    `keyword=`.
  - vrf CHECK (user ran a one-line curl_cffi check at home): search, chapter
    list and page list requested without a token each got 403 with a JSON
    body. The probe's `api_backends.txt` has curl_cffi getting 200 from
    `/api/me`, which never carries a token, so the 403s are the token being
    missing, not curl_cffi being blocked. In the
    captured `ajax_log.txt` the token's length grows with the address and
    addresses sharing a prefix share a token prefix, so it is computed over the
    whole address: a captured URL can't be replayed with another `page=`.
  - DESIGN (approved by the user, "sure"): the browser makes every call and
    the parser catches it. Search types the query into the home page box with
    real keystrokes and Enter and catches the `keyword=` + `page=` request
    (`capture_xhr(type_into=...)`). The chapter list opens the series page and
    presses the pager's own "Next page" button from JS until `meta.hasNext` is
    false (`capture_xhr_pages`); if the API says another page exists but there
    is no button, it raises MangaDoesNotExist instead of returning a partial
    list. Chapters are still identified by number; the API id is only used for
    the reader url, and a repeated number keeps the first (newest) id.
    Rejected: computing the token ourselves (fragile, and it means getting
    around the site's protection), building `page=N` requests, and a plain
    curl_cffi client like mangabuddy.
  - DONE: `scraper/parsers/mangafire.py` rewritten; `scraper/fetchers.py`
    gained `type_into` and `capture_xhr_pages`. Fixtures are trimmed copies of
    the October captures (`tests/test_files/mangafire/{search_titles,
    title_details,chapters_page_1,chapter_pages}.json`); the three old-site
    fixtures go. The parser tests failed on the old code (ImportError), and the
    5 new fetcher tests failed before the fetcher change (TypeError /
    AttributeError). Full suite 557 passed; ruff, format and mypy clean.
  - IDS CHANGED: old ids like `ad-astra-scipio-and-hanniball.lww3` no longer
    work. A folder named after an old id isn't reused automatically;
    `--override_name <folder>` keeps adding to it, and chapters already there
    are skipped because chapter files are named by number. README says so.
  - NOT VERIFIED LIVE. Open questions only a live run answers: paging past
    page 1 (the probe only ever loaded page 1); whether the synthetic button
    click opens an ad tab, as the user's real click did; and why the API
    reports 704 chapters when the newest is 700 (page 1 has 20 distinct
    numbers, 681-700, so it isn't repeats there).
  - DONE-WHEN: at home, `uv run manga-scraper --search naruto --source
    mangafire` lists hits, and `uv run manga-scraper --manga 92kk8-naruto
    --chapters 700 --source mangafire --filetype cbz` saves a complete chapter
    whose pages open. The second also proves the paging: the whole chapter
    list (36 pages for Naruto) is read before any chapter is picked, and the
    walk only ends when the API says there is no next page.

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

- [x] **C4 [QUICK] Probe: auto-namespace output by URL stage + guard overwrites**
  - WHY (surfaced this session): every probe run writes fixed filenames via
    `_write` (plain `write_text`, no clearing); repeated runs into the same
    `probe_out/<host>/` silently clobber recommendation.txt et al. and orphan
    stale `api_*.json`. A tool meant to be run several times per site overwrites
    its headline output with no warning — a footgun (hit during C1).
  - DONE: added `stage_from_url(url, searching)` (pure) classifying a URL into
    chapters/images/search/home by path. `main()` now defaults the out dir to
    `probe_out/<host>/<stage>/` (so the 3 stage runs of one site no longer
    collide without needing `--site`); `--site NAME` still overrides the subpath
    verbatim. Added a `--force` flag + guard: a fresh capture into a non-empty
    out dir is refused (clean argparse error) unless `--force`; `--map-by-example`
    is exempt (it reads existing captures by design). Updated docs/adding-a-source.md
    (no more `--site` heads-up; documents the namespacing + `--force`). Added 6
    tests (stage_from_url cases + namespacing + overwrite-guard). Gates green, 334.

- [x] **C5 [STRUCT][NORTH STAR] Single-entry multi-stage probe** — `probe <url>
  --multi [--search "term"]` captures ALL stages in one run. **DONE** (this
  session): `--multi` runs search (with --search, from the HOME entry) → follows
  the first result to the series page (chapters) → follows the first chapter to
  the reader (images), each into its own <host>/<stage> folder; with --search
  the series url is DERIVED from the query (no slug), without it the entry is
  the series page. Navigation decisions are pure + fixture-tested
  (`first_chapter_link` uses a modal-series-slug rule to pick the MAIN chapter
  list over sidebar/popular links — the fixtures proved naive first-link is
  wrong — plus a text rule for mangago-style hrefs; `first_search_result_link`
  is advisory since result-vs-sidebar is ambiguous). `run_multi` takes an
  injected stage-runner so its sequencing is unit-tested; the real per-stage
  capture reuses `_probe`/`_drive_search`. LIVE-VERIFY: the browser navigation +
  search-from-home trigger are live-only (mocked in tests) — confirm on a real
  run. Possible follow-up: a combined cross-stage recommendation.txt (today each
  stage writes its own).

- [x] **C10 [STRUCT] Probe: recommend the CHEAPEST working fetcher per stage
  (uniform fetcher-ladder reachability)** — DONE (image + page + API stages).
  Delivered: `candidate_image_urls` (page-image
  cluster, src OR data-src, skips data: placeholders + nav imgs) +
  `check_image_ladder` (requests → curl_cffi → cloudscraper, carrying page
  Referer + the live session cookies read via CDP) reporting the cheapest tier
  that returns a real image — replacing the old bare-requests-on-one-data-src
  check. PAGE STAGE (this session): `check_page_ladder(url, browser_html)` runs
  requests → curl_cffi → cloudscraper via `scraper.fetchers` (real
  headers/impersonation, not ad-hoc requests.get) and ranks the `browser` tier
  from the ALREADY-rendered HTML (no second browser launched), reporting the
  cheapest tier that returns real non-challenge content — so the probe stops
  over-recommending a browser when a cheap client works, and `CloudscraperFetcher`
  is now exercised by the probe (closing the headline "cloudscraper never tried"
  gap for the page stage). Wired into `_probe` (writes `page_fetch_ladder.txt`)
  alongside the kept `compare_fetches` (which still supplies the dynamic/JS-
  rendered signal). `_real_page_attempt` is `# pragma: no cover` (real network);
  decision logic via `cheapest_working` is pure + unit-tested (3 new tests:
  cheap-tier-wins, browser-fallback, all-walled→None). `cheapest_working` + url
  extraction stay pure/fixture-tested; per-tier GET injected (mocked in tests).
  API STAGE (this session): `_check_api_backends` now also tries cloudscraper --
  but only when requests AND curl_cffi both failed (no extra request to an
  endpoint a cheaper client already reads, Req 7.1); `backend_verdict` gained a
  `cloudscraper_ok_json` tier (requests < curl_cffi < cloudscraper < blocked) and
  the "cloudscraper" verdict flows through `_pick_default_fetcher` /
  `_verdict_to_fetcher` unchanged (both already pass through any non-blocked
  verdict). +1 pure test for `backend_verdict` ranking. So cloudscraper is now
  exercised by ALL stages (page + image + API) and every stage reports the
  cheapest working fetcher. The original verbose analysis below is retained for
  historical context.
  WHERE the content is (URLs/endpoints/selectors); it is weak at finding the
  LEAST-INVOLVED / FASTEST way to GET it. That second question is the high-value
  one: a parser that opens
  a browser per chapter is a drag (cf. the "1 browser per chapter" observation)
  — if curl_cffi/requests works, the parser should use it and skip the browser
  entirely. Today the "can our fetcher get it?" check is inconsistent across
  stages and never tries the full ladder:
    * **page** (any stage's HTML, `fetch_recommendation.txt` / `compare_fetches`)
      — tries plain **requests vs browser** only; skips curl_cffi + cloudscraper,
      so it OVER-recommends a browser (the documented "under-sells curl_cffi").
    * **API endpoints** (`api_backends.txt` / `_check_api_backends`) — tries
      **requests + curl_cffi** (best coverage), but not cloudscraper.
    * **images** (`image_check.txt` / `_check_image`) — weakest: plain
      **requests bare + Referer** on the FIRST `data-src` sample only; no
      curl_cffi/cloudscraper, no session cookies, and skipped entirely when
      images come via `<img src>` / API / `__NEXT_DATA__`.
  - GAPS (all to close):
    * no stage exercises the FULL ladder requests → curl_cffi → cloudscraper →
      browser; **`CloudscraperFetcher` is never tried by any check**;
    * HTML-served search/chapters (mangago, manganelo/nato/kakalot family) only
      get the page-level requests-vs-browser signal → curl_cffi/cloudscraper
      blind spot;
    * checks re-implement ad-hoc `requests.get`/`curl_cffi` instead of exercising
      `scraper.fetchers` (real headers/session/impersonation the parser uses);
    * images: no session cookies + Referer carried from the live browser session.
  - DONE-WHEN: for EACH stage, the probe identifies the data source (HTML page /
    API endpoint / image URL) and runs the SAME real fetcher ladder via
    `scraper.fetchers` (RequestsFetcher → CurlCffiFetcher → CloudscraperFetcher →
    BrowserFetcher), carrying the captured session cookies + page Referer where
    relevant, and reports the **cheapest fetcher that works per stage** (or
    "needs browser session"). Images handle src/data-src/API/next_data sources
    and check several URLs, not one. Goal stated as performance: prefer the
    fetcher that avoids a browser, especially per-chapter. Pure analysis
    unit-tested; network behind the existing fetch seam.

- [x] **C11 [STRUCT] Manual captcha-solve + persistent browser session** — DONE
  (both halves now exist). The honest answer to "what do we do at a hard wall?"
  Today the wall ladder is curl_cffi (TLS impersonation) → cloudscraper (JS IUAM)
  → headful `nodriver` AUTO-clearance, and the cleared session's cookies CAN be
  harvested (`capture_xhr(with_cookies=True)` reads them via CDP) and reused by
  cheap fetchers (`download_image(cookies=...)` / curl_cffi).
  - DONE half 1 (human-in-the-loop solve): `_wait_for_content` detects a
    remaining interactive challenge (strong-text markers), brings the off-screen
    window on-screen + prompts the user, and waits (indefinitely while the
    challenge is on-screen; Ctrl-C-interruptible) for them to solve it, then
    resumes. (kakalot session + the live-fix follow-ups.)
  - DONE half 2 (persistent session): the shared `_BrowserRuntime` (one browser
    for the whole process) + opt-in persistent profile (`BROWSER_PROFILE_ENV`,
    item (c) above) means a manually-cleared session AND its user_data_dir
    persist across calls and across runs. No more per-call throwaway browser.
  - Original notes (now historical):
  - DONE-WHEN: (a) an opt-in persistent browser profile (reuse `user_data_dir`
    across runs so a solved challenge + cookies survive); and/or (b) an
    interactive flow: detect challenge → prompt user to solve in the headful
    window → wait for clearance → harvest cookies → continue on curl_cffi.
    Gated behind a flag/env (never blocks an unattended/CI run); the
    challenge-detection + cookie-harvest seams reuse `probe.detect_challenge`
    and `capture_xhr(with_cookies=True)`. Motivating case: manganato images
    ("Just a moment…") if curl_cffi/cloudscraper can't clear it.
  - RELATED FINDING (mangago live run): the per-call browser model causes TWO
    visible symptoms beyond captchas, both fixed by the same persistent-session
    work: (1) one download opens ~3 browsers (search + chapter list + reader),
    since every `BrowserFetcher().get()` launches a fresh Chrome; (2) a harmless
    but alarming `Exception ignored in __del__ ... ValueError: I/O operation on
    closed pipe` prints at shutdown — `BrowserFetcher` uses `asyncio.run()` per
    call (fresh event loop each time), so the nodriver subprocess transports are
    GC'd after their loop closed and their `__del__` touches dead pipes. It
    fires AFTER a successful download (no functional impact). A persistent
    browser + single long-lived event loop reused across calls would remove the
    repeated launches AND the shutdown noise. (Quick partial band-aid: a short
    `await` after `browser.stop()` so transports close inside the loop — but the
    real fix is session reuse.)

- [x] **C2 [PROBE] Re-probe + fix-or-retire mangago / mangapark** (your live runs)
  - They scrape Qwik build-hash selectors (`q:key="zn_2"`, `"8t_8"`) + have
    cloudflare-403 notes → likely already broken. Follow the "Re-probing an
    existing source: fix or retire" playbook in docs/adding-a-source.md:
    re-probe the 3 stages (auto-namespaced now, C4), compare each to the parser's
    assumptions, then fix (update selectors / regenerate via scaffold) or retire
    (delete parser+tests+fixtures, drop the `_SOURCE_MODULES` line — see playbook
    step 3b).
  - LIKELY ALSO RETIRE: `mangareader` (mangareader.net is dead — the A2 stale
    default + D3 rename both point at this). Confirm down, then retire per the
    playbook. NOTE: `tests/conftest.py` + `test_cli.py` use `mangareader` as the
    default-source-flows-through fixture — retiring it means switching those to
    another registered source (or a fake), so it's not a pure delete.
  - DONE-WHEN: each of mangago/mangapark/mangareader is either working+fixtured or
    cleanly retired (no dangling refs across scraper/ + tests/ + docs/).
  - PROGRESS (this session, via tools/probe_batch.py 9x3 sweep):
    * **mangapark → RETIRED** (mangapark.io parked: router.parklogic.com
      'Privacy error'). Parser/registry/exemption/README removed; commit pushed.
    * **mangareader → RETIRED** (mangareader.net parked, same). Plus the
      test-harness detangle (ALL_PARSERS→mangakaka, default source→mangabuddy,
      removed the mangareader_*/mangafast_* conftest fixtures + unused params).
    * **mangafast → RETIRED** (mangafast.net parked → DHgate/redirect). Its
      scaffold-sample volume HTML relocated to tests/test_files/html_samples/.
    * **mangago → FIXED** (alive). Site changed: chapter urls now
      /read-manga/<slug>/mr/v<VOL>/c<CHAP>/pg-1/ and reader images are
      <img id=pageN> (q:key='zn_2' gone). Rewrote chapter/image stages (mirrors
      mangabuddy number→href map), promoted 3 captures to fixtures + 12 tests.
    * STRICT-OPTIONAL: all per-module exemptions removed (pyproject) — retired
      three are gone, mangago rewritten compliant. R4 hand-off complete.
  - STILL OPEN (alive, lower-risk): **mangakaka** + **manganelo** probed LIVE via
    plain requests (api=yes) incl. search — their stale "cloudflare-403 on
    search" comments look outdated; verify the parsers still parse current markup
    (likely no-ops / comment cleanup; both already have fixtures+tests).
    **manganato** chapters+search LIVE (requests) but the IMAGES/reader page is
    CF-walled ("Just a moment…"); needs the C10 check (does curl_cffi/cloudscraper
    clear it?) before deciding fix-vs-leave — ties to C11.
  - KAKALOT-FAMILY DRIFT → **FIXED** (this session, fixture-backed):
    * Engine rewritten to the mangabuddy-style {number: href} map (number from
      anchor text, volume_url returns stored href); subclasses simplified to
      base_url/manga_path(=/manga/<slug>)/page_img_attr(all `src` now — readers
      dropped data-src). Search reads `div.story_item` (manganato's stale
      `search-story-item` overrides removed). Refreshed fixtures from captures;
      rewrote test_kakalot.py (fetch seam mocked) + removed stale
      test_mangakaka.py/conftest fixtures. Gates green.
    * STILL OPEN: **manganato images** — the natomanga reader page was CF-walled
      ("Just a moment…") in the probe; the rewritten page_urls uses fetch_soup
      (curl_cffi). Needs a live check that curl_cffi clears it; if not, it's the
      C10/C11 fetcher-escalation case. (manganato chapters/search work.)
  - NET C2 STATUS: mangapark/mangareader/mangafast RETIRED; mangago + the
    kakalot trio (manganelo/manganato/mangakaka) FIXED against fresh captures;
    all strict-optional exemptions removed. Loose end RESOLVED — see C2-kakalot-images
    below: the kakalot reader is an unsurmountable interactive-Turnstile +
    browser-only-CDN wall; download is now documented-unsupported (fail fast).

- [x] **C2-kakalot-images [PROBE] kakalot-family page-image download — VERDICT: not automatable**
  - WHY: the C2 loose end ("does curl_cffi/cloudscraper clear the natomanga/
    nelomanga reader wall?"). Live-tested extensively on the kakalot family
    (manganelo=nelomanga.net, manganato=natomanga.com, mangakaka=mangakakalot.gg).
  - FINDING (evidence-backed, user live runs + probe): the reader page is gated
    by an **INTERACTIVE Cloudflare Turnstile** (manual "Verify you are human"
    click required PER CHAPTER — only clears when the browser window is
    foregrounded) AND the **image CDN (2xstorage.com / waitst.com, hosts rotate)
    serves bytes only to the live browser session**. Every image-extraction path
    was defeated: curl_cffi / cloudscraper (403 even with cookies + Referer),
    direct navigation (blocked), `Network.getResponseBody` (-32000, body not
    retained), canvas (CORS taint), `Fetch.getResponseBody` (nodriver multi-
    session routing bug → "Fetch domain not enabled"; pausing responses hangs the
    page and forced a manual kill).
  - DECISION (agreed with user): unsurmountable wall — leave it in a good
    non-hanging state, keep the learnings. `kakalot.py::page_urls` now fails fast
    with a clear message (no browser, no hang); the hanging Fetch-interception
    capture (`fetch_rendered_images` + JS helpers) was removed from fetchers.py;
    README documents the limitation; tests assert the fail-fast behaviour. Search
    + chapter listing still work (787 Naruto chapters parsed via scroll-to-load).
  - Gates green, 379 pass.

- [x] **Browser toolbox primitives (kept from the kakalot experiment)** — reusable
  `BrowserFetcher` capabilities proven useful even though kakalot download is a
  dead end. All live in `scraper/fetchers.py`:
    * **Focus emulation** (`_focus_window`) — CDP `setFocusEmulationEnabled` +
      `setWebLifecycleState('active')` + `bring_to_front`, so a *passive* CF
      challenge clears even when the OS window is backgrounded (a background
      process can't reliably steal real focus). DONE.
    * **Challenge-detect + manual-wait** (`_wait_for_content` + `_looks_like_challenge`)
      — detects a CF/Turnstile interstitial, prints a one-time "click the Verify
      you are human checkbox" prompt, and waits INDEFINITELY while a challenge is
      on-screen (human-in-the-loop solve); only the non-challenge stall is
      timeout-bounded so a real load problem can't hang forever. DONE.
    * **Scroll-to-load** (`get_after_scroll`, `_count_elements_js`,
      `_scroll_to_bottom_js`) — drives infinite-scroll lists to the bottom until
      the element count stops growing before parsing. DONE (used by kakalot
      `all_chapter_ids`).
  - FUTURE PRIMITIVE (recorded, NOT built — no unused code): a **"click to reveal
    / show all chapters"** helper for sites that hide the full chapter list behind
    a button. Build it only when a real source needs it.
  - RELATED: the human-in-the-loop challenge wait overlaps C11 (manual captcha-
    solve + persistent session); the persistent-profile half of C11 is still open.

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

- [x] **D1 [QUICK] Rename `SearchResult.chapters` → `latest_chapter`** — DONE.
  - Renamed the field in `new_types.SearchResult` (+ `__post_init__` normalization)
    with a docstring marking it DISPLAY-ONLY ("Latest Volume" column hint, meaning
    varies by site, never used for selection — that's the real chapter list via
    scraper.selection). Updated all 7 parser construction sites (mangafire,
    mangafast, kakalot, mangapark, mangareader, mangago, mangabuddy), the
    `menu.table()` read, the scaffold's emitted `_parse_search_items` + comments,
    and all test fixtures (helpers METADATA, mangakaka/mangafast dicts via
    as_search_results, mangabuddy/mangafire/menu). Renamed the WHOLE path, not
    just the kwarg: feeding local vars `chapters` → `latest_chapter`, and the
    kakalot/manganato `_chapters()` method → `_latest_chapter()`. Gates green,
    339 pass. (Chose `latest_chapter` over `chapters_hint`: it says what the value
    IS, not just what not to trust; matches the column.)
  - NOTE: the menu column HEADER is still literally "Latest Volume" (audit L7 —
    header vs field-name mismatch). Left as-is: it's user-facing display text and
    "Latest Volume" conveys intent; renaming the field was the substantive fix.

- [x] **D2 [QUICK] `menu.py table()` unicode + magic number** — DONE.
  - Truncation magic number → `TITLE_MAX_WIDTH` constant (done in B1 part 2).
  - Unicode: removed `.encode("ascii", errors="ignore")` which silently dropped
    non-ASCII characters (mangling JP/accented titles to ""); the title now
    renders as-is. Test asserts a JP title (鋼の錬金術師) and an accented one
    (Pokémon) survive in the rendered table. Gates green.

- [~] **D3 [DECISION] Project rename** — DECISION: **keep the name** for now.
  "MangaReaderScraper" reads fine generically ("scraper for manga-reader sites"),
  it doesn't have to mean the dead mangareader.net; the package (`scraper/`) and
  CLI (`manga-scraper`) are already neutral. User would PREFER a rename but it's
  deferred: low value + friction (repo rename, clone URLs) and needs a chosen
  name + the GitHub repo rename (user action). Revisit if/when a name is picked.
  - DONE alongside this: the actual stale-doc fixes hiding in D3 -- README
    quick-start refreshed (volume→chapter wording + retired `mangareader` demo
    source → `mangabuddy`); pyproject `Homepage` repointed to the fork
    (`qam4/...`) with `Upstream` kept for attribution; historical-snapshot banner
    added to docs/code-review.md + docs/refactoring-plan.md so their pre-refactor
    content isn't mistaken for current.

- [x] **D4 [QUICK] De-personalize `bundle.py`** — DONE: `WRITER_DEFAULT` is now
  the neutral `"Unknown"` (was the maintainer's name, which shipped as the
  `<Writer>` of every user's comic). Added `_configured_writer()` reading an
  optional `[config] writer` ini override, falling back to neutral. The dead
  `multi_process = True` toggle + `else` branch were already removed in A6.
  - NOTE: D4 is the QUICK de-personalization (neutral fallback + configurable).
    Actually EXTRACTING the real author is the bigger E1 feature below — D4 just
    stops shipping the maintainer's name as every comic's writer in the meantime.

## Wave E — features (new capability, not cleanup)

- [x] **E1 [STRUCT-lite] Extract author(s) into the pipeline → ComicInfo `<Writer>`**
  - WHY: nothing in the pipeline carried an author, so every ComicInfo.xml got
    the neutral/maintainer default. The data IS available; the plumbing didn't
    exist.
  - DONE (the full SHAPE below, single comma-separated string):
    1. `Manga` gained `author: Optional[str] = None` (set parent-side).
    2. `BaseMangaParser.author()` hook returns None by default; parsers override.
    3. `MangaBuilder.get_manga_volumes` sets `self.manga.author` from the hook,
       best-effort (try/except — a failing lookup must not abort the download).
    4. `bundle.py` `Bundle.__init__` uses `manga.author or _configured_writer()`
       — extracted author wins, else the neutral/ini default (never a person).
    5. MangaFire implements `author()`: fetches the `/manga/<slug>` series page
       (BrowserFetcher) and parses `a[itemprop="author"]` via the pure
       `_authors_from_html` (de-dupes, joins multiple with ", ").
  - TESTS: `_authors_from_html` against a captured-shape `series_page.html`
    fixture (single + multiple/de-dupe + absent); `author()` fetch+parse and
    fetch-failure→None; builder sets author from the hook + swallows a failing
    hook; new `test_bundle.py` for the writer precedence. Gates green, 357 pass.
  - DECISIONS MADE: single comma-separated string (not List) — maps directly to
    ComicInfo `<Writer>`; author surfaced only in `<Writer>` for now, NOT in
    SearchResult/menu (no demand, keeps SearchResult lean).
  - COST NOTE: MangaFire's `author()` is a SECOND browser navigation on top of
    `all_volume_ids`. Acceptable (once per download, best-effort) but a future
    optimization could capture the series HTML during that existing session.
  - SUPERSEDES D4's interim fix: the `<Writer>` is now the real author when
    available; D4's neutral fallback remains for when it isn't.

## Wave F — download robustness (user-reported: incomplete chapters on first run)

User observation: running "download all volumes" often leaves some chapters
incomplete on the first pass; re-running a few times eventually completes them.
Two distinct root causes found:

- [x] **F1 [QUICK-ish] `download_image` retries have NO backoff (the real culprit)**
  - WHERE: `fetchers.download_image` — 5 tries but the loop just did
    `attempt += 1` with NO sleep between attempts. It hammered the CDN as fast as
    it could fail. CDN failures are usually transient/rate-limit, so instant
    back-to-back retries are the worst response → missing pages → incomplete
    volume. This is the path mangafire/mangabuddy use.
  - DONE: added exponential backoff + jitter between attempts —
    `min(backoff_base * 2**(n-1), backoff_cap)` plus up to half that as random
    jitter (de-syncs the parallel workers), sleeping between tries but NOT after
    the final one. `backoff_base` (0.5s) / `backoff_cap` (30s) are params with
    sane defaults. 4 unit tests (patch `time.sleep`): retry-then-succeed sleeps
    n-1 times, exhausted sleeps max_tries-1, and waits strictly grow
    (>=1/2/4/8s with backoff_base=1). Gates green.
  - FOLLOW-UPS: 429/503-aware longer waits + Retry-After → F4; unify with
    base.page_data's loop (one retry policy) — still worth doing, ties to C3.

- [x] **F2 [QUICK-ish] Incomplete-volume files are never cleaned up**
  - WHERE: `manga.py` `Manga.add_volume(complete=False)` writes the volume with a
    `-incomplete` suffix; `volume_exists` only checks the COMPLETE name, so a
    later run re-downloads (good) and writes the clean file — but the stale
    `-incomplete` file was NEVER deleted → orphans accumulate, complete+incomplete
    pairs coexist.
  - DONE: parent-side cleanup in `_add_download_to_manga` — when a volume
    completes (`download.complete`), `_remove_incomplete_sibling(file_path)`
    deletes any leftover `<...>-incomplete.<ext>`. Added `_incomplete_path` helper
    (inserts the suffix before the extension, mirroring add_volume). 2 tests: the
    stale incomplete is removed once the volume completes; the path helper builds
    the right name. Gates green, 338 pass.
  - NOTE: F1 reduces how OFTEN incompletes happen; F2 cleans up when they do.

- [x] **F5 [QUICK-ish] CBZ corruption: bare ZipFile in bundle.py + non-atomic writes**
  - WHERE: `bundle.py` opened the volume archive bare (`z = zipfile.ZipFile(path,
    "w")` ... `z.close()` at the end). If ANY step in between raised (bad chapter
    file, failed extract, interrupt), `z.close()` was never reached → the zip's
    central directory was never written → a `.cbz` that exists but is UNREADABLE
    (`BadZipFile`). The `writers.py` CbzWriter/PdfWriter used `with`/`c.save()` so
    they closed cleanly, but still wrote DIRECTLY to the final path → a failure
    mid-write left a partial file there too.
  - DONE: added the shared `utils.atomic_write_path(final)` context manager —
    write to a `.part` sibling, `os.replace` onto the final path on clean exit
    (atomic publish), unlink the partial on ANY exception (final untouched).
    Applied it to all three write sites: bundle.py volume cbz (now `with
    atomic_write_path(...) as tmp_cbz: with ZipFile(tmp_cbz) ...`; the mid-bundle
    extract failure now propagates so the partial is dropped instead of a bare
    `return` that left a half-built archive), CbzWriter, and PdfWriter. Tests:
    helper publishes-on-success / leaves-nothing-on-failure / preserves-existing-
    final-on-failure; CbzWriter leaves no file when zipping raises. Gates green,
    343 pass.
  - CONSOLIDATION: this is the one shared "non-corrupting file output" primitive
    the three writers were each missing — fixes the corruption bug AND the
    partial-file problem (same family as F2) in one place.

- [x] **F3 [QUICK, LOW-PRI] Configurable worker pool size (be a gentler default)**
  - WHERE: `manga.py` `_get_volumes_data` hardcoded `Pool(4)`; `bundle.py` used
    `Pool()` (all cores). No way to turn concurrency down to be kinder to a site.
  - DONE: added `utils.resolve_jobs(cli_jobs)` — precedence CLI > ini
    `[config] jobs` > CPU-aware default `min(4, cpu_count)` (helper
    `_default_jobs`); always >= 1, invalid ini warned + ignored. `--jobs`/`-j`
    CLI arg threaded through `download_manga` → `Download` → `MangaBuilder.jobs`
    (used in its `Pool(self.jobs, ...)`) and `bundle` → `Bundle.jobs` (its
    `Pool`). Both pools now honor it; `bundle.py` no longer fans out to all
    cores. 5 resolve_jobs unit tests (CLI wins/floors, ini value, invalid-ini
    fallback, cap-at-4 on many cores, low-core respect); test_cli pops the new
    `jobs` arg like `log_level`. Gates green, 348 pass.
  - NOTE: `jobs` is read from ini via `.get` (not written to base config) so the
    default stays CPU-aware rather than baking a fixed number into new configs.

- [x] **N1 [QUICK, LOW-PRI] Naming drift cleanup (functions outgrew their names)** — DONE
  - SMELL (user): some functions grew past their original job but kept the v1
    name. Clearest: `_extract_text` (Kakalot/Mangago search parsers) returns a
    `SearchResult`, not text → renamed `_parse_search_result`.
    (`_fetch_html`→`_fetch_manga_page` also done in an earlier slice.)
  - BIGGER (DONE): "volume" meant "chapter" throughout the model/parser/
    download/writer/exception/uploader layers — a MEANING-AWARE rename (bundle
    is the one place "volume" is used CORRECTLY = a group of chapters).
    - DONE: renamed only the model/parser/download/writer/exception/uploader
      layers + their tests — `Volume`→`Chapter`, `Manga.volumes`→`chapters`,
      `volume_url`→`chapter_url`, `all_volume_ids`→`all_chapter_ids`,
      `VolumeDownload`→`ChapterDownload`, `get_manga_volumes`→`get_manga_chapters`,
      `download_volumes`→`download_chapters`, `Volume*` exceptions →`Chapter*`,
      the logging adapter `volume` key, and the `vol_*`/`vol_ids`/`vols` local
      abbreviations. Fixture files `*_volume_*.html`→`*_chapter_*.html`.
    - LEFT INTENTIONALLY: `bundle.py`'s group vocabulary (`chapters_per_volume`,
      `create_volume`, `num_volumes`, `volume_cbz_path`, ...); the seam now reads
      `manga_chapters = self.manga.chapters` (alias comment dropped).
    - CLI: renamed `--volumes`→`--chapters` (`-q` unchanged), NO deprecated
      alias kept (personal fork, no external contract). `__main__` helpers
      renamed (`get_chapter_values`, `normalize_chapters`, `chapters` dest);
      search-menu display column "Latest Volume"→"Latest Chapter". The
      `mangafire` real URL path `ajax/read/volume` left as-is (external API).
  - DONE-WHEN: ✅ ruff + mypy + 366 tests green; no `vol_*`/`Volume`/model-layer
    `volume` symbols remain.

- [x] **F4 [STRUCT, LOW-PRI] Respectful adaptive throttling on failure** — DONE.
  - Retry-After: `download_image` special-cases 429/503 and honors a numeric
    `Retry-After` (waits at least that long, capped at 120s), `_parse_retry_after`
    pure + tested.
  - Cross-worker ease-off: `_RateLimitThrottle` (process-wide, thread-safe) -- a
    429/503 in ANY download sets a shared cooldown; every download thread waits
    it out before its NEXT request (consulted once at the top of `download_image`,
    not in the retry loop, so it composes with the per-request backoff). Easy now
    that downloads are threads (shared memory). conftest resets it per-test for
    isolation; 3 new tests (no-op when unlimited, ease-off+clear, sibling ease-off).
  - DONE-WHEN met: repeated rate-limit responses lower the aggregate request rate
    (not just per-request delay); Retry-After respected. Aggregate behaviour
    unit-tested; real-concurrency feel is live-to-confirm but the mechanism is
    proven.

- [x] **G1 [DECISION, LOW-PRI] KCC delivery: submodule vs git-dep vs self-built**
  - DONE in f0efa4a (`bundle` extra, see RESOLVED below); the box was left
    unticked until the backlog review after G3.
  - FINDING: the `kcc` git submodule points at UPSTREAM `ciromattia/kcc` pinned
    to v10.2.0 and is CLEAN (no local patches) -- the original reason for it
    (local threading fixes) is gone now that upstream's `--tempdir` flag makes
    parallel bundling safe. So the submodule is now just a delivery mechanism.
  - CONSTRAINT: can't simply `pip install KindleComicConverter` -- PyPI is stale
    at 5.4.1 (2017, pre-`--tempdir`); KCC ships 10.x only via GitHub/choco/winget.
    KCC's setup.py DOES expose the `kcc-c2e` console script, so it's pip/git
    installable from source.
  - OPTIONS (ranked):
    1. **Keep the submodule** (current). Works; pinned; one manual step
       (`git submodule update --init` + `uv pip install -e kcc/`). Slightly
       unconventional for an unpatched upstream, but not wrong.
    2. **Pinned git dependency** -- `kcc @ git+https://github.com/ciromattia/kcc.git@v10.2.0`
       in a `bundle` extra; `uv sync --extra bundle` installs it. More idiomatic
       for a consumed-not-patched dep; drops `.gitmodules` + the `kcc/` checkout
       + a manual step. NOTE: a direct git-URL dep BLOCKS publishing this project
       to PyPI (PyPI forbids URL deps) -- fine since we install from source.
    3. **Self-manufactured artifact** -- build a wheel from the tag and vendor it
       (`kcc @ file://...whl`) / host a tiny PEP-503 index (GitHub Release/Pages)
       / publish a renamed GPL fork to PyPI. All MORE work than (2) for the same
       result; only actually REQUIRED if we ever publish MangaReaderScraper to
       PyPI (then we'd need KCC on a real index).
  - CAVEAT (all options): KCC hard-requires PySide6 (Qt, heavy) in
    install_requires even for the CLI -- so installing KCC is heavy either way;
    bundling is already opt-in/extra-setup, so that's consistent.
  - ~~DECISION: keep the submodule for now~~ -- SUPERSEDED, see RESOLVED below.
  - RESOLVED (option 2 implemented): this stopped being cosmetic once it caused a
    real failure. The user hit module-import errors from kcc on a fresh laptop
    setup, because `uv pip install -e kcc/` installs OUTSIDE the lockfile: any
    venv rebuild leaves `kcc-c2e` on PATH with all eleven of its runtime deps
    (natsort, psutil, PyMuPDF, numpy, mozjpeg-lossless-optimization, distro,
    packaging, python-slugify, ...) gone. Our `pyproject.toml` declared none of
    them and knew nothing about the submodule, so bundling was one `uv sync`
    away from broken at all times.
    - Added a `bundle` optional extra + `[tool.uv.sources] KindleComicConverter =
      { git = ..., tag = "v10.2.0" }`. `uv sync --extra bundle` now installs KCC
      and its deps reproducibly from `uv.lock`.
    - GIT source, not the `kcc/` path: a path source must build its metadata at
      LOCK time, so `uv lock`/`uv sync` would fail for EVERY user -- bundlers or
      not -- whenever the submodule isn't checked out. That would have been a
      worse regression than the bug being fixed.
    - Verified: `uv lock` resolves the tag to `dc4475bc`, the SAME commit the
      submodule is pinned to. `uv sync --extra bundle --dry-run` swaps the
      editable path install for the git pin.
    - Submodule KEPT (not dropped as option 2 suggested): reading KCC's source
      is what let us diagnose the Panel View / gamma / auto-crop changes. Its
      commit and the lockfile tag must now be bumped TOGETHER.
    - HAZARD to know: a plain `uv sync` (no `--extra bundle`) uninstalls kcc +
      PySide6. That predates this change (kcc was never in the lock); the
      difference is there is now a supported way back. A plain `uv run` does not
      prune -- verified.

- [x] **G2 [TEST] `--bundle` was effectively untested (31% coverage; partly done)**
  - CLOSED after G3: the last two (items 2 and 4) are now tests. Splitting: 7
    chapters at 3 per volume -> `vol1 ch1-3`, `vol2 ch4-6`, `vol3 ch7`, with the
    right chapters inside each. Padding: 9 volumes -> `vol1`..`vol9`, 10 ->
    `vol01`..`vol10`. Bundle dir: `manga_bundle_directory`, else
    `manga_directory`, else the current directory, read through a real
    ConfigParser. Each was checked against a deliberately broken copy of
    bundle.py (span off by one, padding fixed at 1, fallback dropped) and
    failed there.
  - MEASURED: `scraper/bundle.py` is at 31%, 100 of 145 statements never
    executed. `tests/test_bundle.py` covers only `_convert_to_mobi`'s command
    line + error handling, the `<Writer>` source, `_kcc_args` resolution, and
    (new) `is_obsolete`. Untested: `create_volume`, `bundle()`, `extract_cbz`,
    `ceiling_division`, `_get_manga_download_dir`, `_get_manga_bundle_dir`.
  - WHY IT MATTERS: volume naming, the `vol01` zero-padding, the ComicInfo body
    and the internal archive layout are all unpinned, which is exactly why a
    whole session of Kindle-output regressions (Panel View, gamma, page tone,
    chapter-folder renaming) was invisible until the user noticed them ON THE
    DEVICE. A test would have caught the layout change from the stable-index
    rename for free.
  - PROPOSED (no network needed; `tests/test_files/jpgs/*.jpg` are enough to
    build real chapter `.cbz` inputs, and `_convert_to_mobi` gets mocked):
    1. ~~`create_volume` end to end~~ -- DONE. Exact filename pinned as the
       Calibre contract (`test_create_volume_filename_is_the_calibre_contract`),
       single-chapter form, archive layout, up-to-date skip.
    2. `bundle()` volume splitting -- 7 chapters at 3/volume gives 3 volumes with
       the right chapter ranges, and `volume_digits` flips `vol1` -> `vol01` at 10
       volumes.
    3. `extract_cbz` -- bad zip, missing file, pre-existing-directory cleanup.
    4. `_get_manga_bundle_dir` -- the fallback chain to `manga_directory` then
       `os.getcwd()`.
    5. ~~ComicInfo body~~ -- DONE, and the oddity was a real defect, fixed.
       `<Series>` now holds the series name, plus `<Volume>`, `<Title>` (real
       chapter range) and one `<Page Bookmark>` per chapter. Verified with the
       REAL kcc-c2e, offline, on the fixture jpgs: device title went
       `Naruto vol1 ch1-2` -> `Naruto Vol. 01`, and the table of contents went
       `Naruto_chapter_748_700` -> `Chapter 700`.
  - STILL OPEN from the list above: 2 (`bundle()` splitting + `vol01`
    padding) and 4 (`_get_manga_bundle_dir` fallbacks). Item 3 is MOOT:
    `extract_cbz` no longer exists (G3 item 2 copies pages archive-to-archive).
    Coverage after G3: `scraper/bundle.py` 97% (was 31%).
  - CALIBRE CONTRACT (recorded so it is never "cleaned up" again): the volume
    filename `<series> - <series> vol<N> ch<a>-<b>.cbz` repeats the series name
    ON PURPOSE. Calibre's built-in CBZ reader only parses a ComicBookInfo zip
    comment, never ComicInfo.xml (calibre `ebooks/metadata/archive.py`,
    `get_comic_metadata`, read from master), so the user's Calibre takes .cbz
    metadata from the filename via a regex. The tested regex is in the README.
    On a fresh Calibre the default regex puts the volume name into Author --
    which is exactly what the user hit on a new computer.
  - ~~ALSO SPOTTED: `extract_cbz` did `logger.error("An error occurred:", e)`~~
    -- RESOLVED by removal: the function is gone (G3 item 2), and a failed
    chapter read now logs `Failed to build <volume> (<cause>); skipping volume`.
  - PRIORITY: high for a bundling change, low while bundling is untouched. The
    `is_obsolete` missing-chapter crash found while writing G2's analysis was
    fixed separately (with a test that was verified to fail first).

- [x] **G3 [STRUCT] Rebuild/skip dependencies across download + bundle (6 items)**
  - ORIGIN: user asked whether the mtime "dependency scheme" could get in the
    way silently. A review of every skip/reuse decision found six defects, two
    reproduced before anything was written. All six fixed in order, each with a
    test verified to fail on the pre-fix code.
  - 1. **Rebuilding a MOBI kept the stale one and reported success.**
    REPRODUCED with the real kcc-c2e + kindlegen: KCC never overwrites --
    `getOutputFilename` (comic2ebook.py) writes `<stem>_kcc0.mobi` beside an
    existing target -- and `_convert_to_mobi` then checked that `<stem>.mobi`
    existed, which the STALE file satisfied. Also in the old v5.6.1 fork, so it
    predates the KCC bump. FIX: convert into a fresh per-volume temp folder
    inside mobi/, require exactly `<stem>.mobi` there, `os.replace` onto the
    target; temp removed in `finally`; failure leaves the previous MOBI intact.
    Re-run of the real repro: one MOBI, new content, no `_kcc0`, no leftovers.
    NOTE: this also made the advice "delete only the volume .cbz" (given to the
    user, then corrected) wrong before the fix.
  - 2. **Rebuilds were blind to settings and content.** A volume counted as
    current when newer than its chapter files; a MOBI when newer than its
    volume. So scraper updates, author changes and `kcc_args` edits never
    reached existing files. FIX: (a) volume = rebuild when the ComicInfo.xml it
    would get differs from the one inside it (plan first, page counts from the
    chapter zips' namelists; the same list is counted and copied, so bookmark
    indices can't drift); (b) `BUNDLE_FORMAT` in ComicInfo `<Notes>` (KCC
    ignores it) -- bump it and every volume self-invalidates; (c) MOBI =
    `<manga>/.mobi-stamps.json` records kcc args + KCC version per MOBI,
    outside mobi/ so a Kindle copy carries no clutter; missing stamp = rebuild
    once. Real kcc-c2e: conversions per run 1, 0, (args changed) 1, 0.
    Side effects: `extract_cbz` and `_get_manga_download_dir` removed.
  - 3. **Chapter identity included the list position.** REPRODUCED: the site
    inserting chapter 9.5 re-downloaded 10, 11, 12 under new names and left both
    copies (8 files for 5 chapters). Files were `<name>_chapter_<pos>_<id>`;
    the "stable index" fix (2ab81c8, recorded under UX additions) made the name
    independent of the SELECTION but not of the site's list changing. FIX:
    `<name>_chapter_<id>`; position kept only in memory (`Chapter.order`) for
    reading order. Old-style files are renamed in place (newest wins), extras
    MOVED to `.superseded/`, never deleted; only ids the site lists are touched.
    Volume folders got a position prefix (`003_<stem>`) because KCC
    natural-sorts them and opaque ids / `700.10` would otherwise misorder the
    bookmarks -- proven: the ordering test fails with the prefix removed.
    `BUNDLE_FORMAT` -> 3.
  - 4. **Bundling needed the site online** (chapter list + author fetched on
    every run, though all inputs are local). FIX: downloads write
    `<manga>/.series.json` (source, url, name, author, FULL ordered list);
    `--offline` with `--bundle` rebuilds from that + files on disk, never
    touching the network or even the configured source. Missing chapter files
    are registered incomplete, exactly as online, so volume boundaries match.
    Equivalence test (online then offline -> both volumes evaluated and found
    current, nothing rebuilt) PROVEN falsifiable: dropping the author or a
    chapter from the offline Manga makes it fail.
  - 5. **Orphaned volumes were never mentioned** (other `--bundle N` /
    `--chapters` sets, `_kcc0` duplicates; Calibre imports the lot). FIX:
    `bundle()` ends with a warning listing them; reports only. Found while
    doing it: the MOBI path was `cbz_path.replace("cbz", "mobi")`, which also
    rewrote "cbz" inside series names ("Xcbz Saga" -> "Xmobi Saga" folder).
    Naming now has ONE source, `Bundle._volume_paths`.
  - 6. **The series name forked the download folder**: search names it after
    the result title, `--manga` after the slug -> two folders, full
    re-download. FIX: reuse a folder whose `.series.json` has the same source +
    url (`--override_name` always wins); for record-less old folders a
    lookalike name is only WARNED about, with the `--override_name` to use --
    never adopted on a guess.
  - USER-VISIBLE ON NEXT RUN: chapter files renamed (no re-download); every
    volume rebuilt once (format 3) and every MOBI once (no stamp yet). Uploads
    (unmaintained) now use the new chapter names too.
  - NOT VERIFIED: anything on the live sites or a device. All checks were
    offline: unit tests, plus the real kcc-c2e/kindlegen on fixture images.
  - REGRESSION, FIXED: item 6 broke CI on Linux (15 failures, all one frame).
    `_existing_series_folder` called `Path.is_file()` on every folder's
    record, and on 3.13 that raises PermissionError for a folder the user
    can't enter -- the runner's `/tmp/systemd-private-*`. Passed on Windows
    because `C:\tmp` has none. Fix: skip unreadable folders; test fakes the
    PermissionError so it reproduces on any OS (failed before the fix).

- [x] **G4 [TEST, LOW-PRI] Tests read and write the machine's real `/tmp`**
  - DONE: the autouse settings fixture is now per test and points
    `manga_directory` at a new `manga_directory` fixture (the test's
    `tmp_path`); path assertions use it. `test_utils` uses `tmp_path` as home.
    The module teardowns that DELETED `/tmp/dragon-ball`, `/tmp/cool_mo_deep`,
    `/tmp/smelly_pancakes`, `/tmp/.config` and `/tmp/Downloads` are gone.
  - MEASURED with a `sys.addaudithook` pytest plugin logging every open,
    listing, mkdir, rename and delete under the real `/tmp` (`C:\tmp` here),
    excluding pytest's own basetemp: 309 touches from 21 tests before, 0 after,
    504 passed both times.
  - ALSO FOUND: `test_settings_creates_base_config` never tested creation; it
    found the ini the previous test had written. It now asserts the file is
    absent first.
  - FOUND by the G3 regression: `tests/conftest.py` (autouse
    `mocked_manga_settings`) sets `manga_directory` to `"/tmp"` for every
    test, so results depend on what else is on the machine (`C:\tmp` on
    Windows) and tests can see each other's files. The G3 tests use
    `tmp_path` instead, which is the fix pattern.
  - COST: unknown until counted -- every test relying on the autouse fixture's
    `/tmp` path (e.g. `tests/test_download.py` asserts `/tmp/...` paths).

- [x] **G5 [QUICK, LOW-PRI] A non-object `.series.json` still crashes two readers**
  - DONE: `load_offline_manga` raises `OfflineSeriesNotFound` ("Unreadable
    offline record ...: not a JSON object"); the folder lookup skips such a
    folder like an unreadable record. Tests failed on the old code: 5 with the
    AttributeError, and `null` in the lookup with a wrong "looks like this
    series but has no .series.json" warning (null parsed to None, which the
    code read as "no record").
  - FOLLOW-UP, DONE: the field types inside a valid object. `load_offline_manga`
    now refuses, as "Unreadable offline record", a `chapters` that is not a
    list of strings/numbers, a non-string `author`, and a chapter id listed
    twice. On the old code these gave a TypeError (`"chapters": 5`), junk ids
    (`"700"` read as 7, 0, 0; a dict or `true` stringified), a
    ChapterAlreadyPresent traceback (repeated id), or an author that only
    failed later in the ComicInfo build; all 6 cases failed there. Numeric
    ids (a hand-edited list) are still accepted.
  - `_existing_series_folder` and `load_offline_manga` call `record.get(...)`
    on whatever `json.loads` returned, so a record that parses as a list or a
    string raises AttributeError (download fails; `--offline` shows a
    traceback instead of OfflineSeriesNotFound). Not observed; we only ever
    write objects. Fix: treat a non-dict like an unreadable record, with a test.

- [ ] **G6 [DEVICE] MOBI pages look slightly too tall on the Kindle**
  - REPORTED by the user: pages slightly too tall vertically, seen on the
    device, during the KCC v10.2.0 output review. Not reproduced or measured.
  - CHECKED in that session: the device-profile theory did not hold, and the
    theory that KCC 10.x's crop cap (10% per edge) was responsible was
    weakened by the fixture pages' proportions. The detailed reasoning was not
    written down at the time.
  - KNOWN: our default `kcc_args` pass no `-p`, so KCC's default profile `KV`
    applies (read in `kcc/kindlecomicconverter/comic2ebook.py`) unless the
    user's ini sets one.
  - NEEDS from the user: the Kindle model, and what "too tall" looks like
    (cut off at top/bottom, stretched, or a page taller than the screen so it
    scrolls). A photo or screenshot of one page would settle which.
  - OFFLINE CHECK, DONE (real converters, EPUB output, KV profile): the old
    fork run from `git -C kcc archive 627afbb` (v5.6.1-28, EPUB generator
    5.6.1) with its old flags (`-u`), against v10.2.0 with ours, with and
    without `--hq`.
    * NO DISTORTION in any of them. A test page of 100x100 squares comes out
      square (w/h 1.004 old fork, 1.001 v10 without --hq, 1.000 v10 default)
      and the grid keeps its 0.700 proportions.
    * THE DIFFERENCE IS CROPPING. v10's default crop (`-c 2`: margins + page
      number) removes a bottom page number and white margins that the old fork
      kept, so the art is drawn larger: on the square-grid page the art spans
      1280 of the 1448 px page height in v10 vs 1168 in the old fork (+9.6%);
      on a page built like a scan (fixture art on a white page with margins
      and a page number) 93.0% vs 83.1% of the screen height. With `-c 1`
      (margins only) 85.2%, `-c 0` (off) 76.1%.
    * `--hq`: images are 1.5x (e.g. 1513x2172) inside a viewport at device size
      (1008x1448); the art's size on screen is the same as without it.
    * The committed fixture jpgs have art to the edges (no margins), which is
      why they showed none of this.
  - LEADING HYPOTHESIS (INFERRED, needs the device): "slightly too tall" = the
    art now fills ~10% more of the screen height because the page number and
    margins are cropped away. Settle it on the device: rebuild one volume with
    `kcc_args = -u --hq -g 1.8 --metadatatitle 1 -c 1` and compare. If that
    looks like the old books, the fix is a default change, the user's call.

- [x] **G7 [BUG] `--output` / `-o` is accepted but does nothing**
  - HISTORY (git log -G): it worked in the first version (58500e8, 2017,
    passed to the PDF writer) and stopped in 4b872b9 (2018-12-30), when the
    converter refactor wrote to the config constant and the CLI stopped
    passing it on. The 2019 references only echoed it back from cli().
  - DECISION (user): make it work.
  - DONE: `-o` is the download folder for the run. It reaches every download
    call (including the one after a fallback search) and `--offline`, through
    a `directory` on `Download`, `MangaBuilder`, `Manga` and
    `load_offline_manga`; `_download_root` falls back to the ini
    `manga_directory`, read when used. The default is now None instead of the
    ini value read at import. Bundle output is unchanged
    (`manga_bundle_directory`). 3 of 4 new tests in tests/test_cli.py failed on
    the old code; the 4th guards the default.
  - READ in code: `scraper/__main__.py` defines `--output` (default: ini
    `manga_directory`) and nothing in `scraper/` reads `args["output"]`;
    downloads always go to the ini `manga_directory`. The README documents it
    as "Directory to save downloads". So `-o somewhere` silently saves
    elsewhere. Not run.
  - DECISION for the user: make it work (thread it through to `Manga`'s paths,
    and say whether it also moves the bundle directory), or remove the flag
    and the README line.

- [x] **G8 [TEST, LOW-PRI] Importing `scraper.__main__` reads the real user ini**
  - DONE: `scraper/__main__.py` reads settings in `get_parser()` instead of at
    import; the CLI fixture patches `scraper.__main__.settings`; a new autouse
    `isolated_home` fixture gives every test an empty home (HOME and
    USERPROFILE). A subprocess test imports `scraper.__main__` with an empty
    home and checks no ini appears; it failed on the old code (the ini was
    created).
  - MEASURED: the open-file audit had shown one read, but `_read_settings` is
    cached, so later reads never touched the disk. Wrapping `_read_settings` to
    count every read of the real `~/.config/mangascraper.ini`: 62 reads from
    collection plus 50 tests (test_bundle 13, test_cli 3, test_download 4,
    test_manga 30) before, 0 after; 521 passed.
  - MEASURED with the G4 audit hook pointed at `~/.config`: one open of
    `~/.config/mangascraper.ini`, during test collection. Cause, read in code:
    `scraper/__main__.py` line 20 runs `CONFIG = settings()["config"]` at
    import, and `settings()` creates the ini when it is missing
    (`scraper/utils.py`). INFERRED from that, not run: a test run on a machine
    with no ini writes one into the real home. The `mocked_manga_env_var_cli`
    fixture patches `CONFIG` only after that import.

- [ ] **G9 [QUICK, LOW-PRI] `.gitignore` hides new test fixtures**
  - FOUND while preparing the C12 commit: the four new MangaFire fixtures did
    not show in `git status`. `git check-ignore -v` names `.gitignore` line 4,
    `*.json`; line 2 is `*.jpg`. The five tracked JSON fixtures and the two
    jpgs in `tests/test_files/jpgs/` were force-added. INFERRED, not run: a
    fixture added without `-f` passes locally and fails CI at collection, since
    the test modules read their fixtures at import.
  - FIX (a config edit, needs the user's OK): add `!tests/test_files/**` after
    the custom block, then check `git status` lists an untracked fixture.

---

## Suggested order
A (all) → B (B1+B2 designed together, B2 test first) → C (probe-gated) →
D (fold D3 into B if rebuilding). Start nibbling at Wave A.

## Done this session (for reference)
- Probe-assisted source authoring spec: all 19 tasks (Phases 1-3 + html image
  mode), committed + pushed + CI green.
- A2 (stale default → mangabuddy) + A5 (total_pages count) — commit 864c363.

## Threading migration (process Pool -> ThreadPool + shared browser session)
Audit conclusion (with user): the process-per-chapter model mis-placed the
browser and caused the spawn-class bugs. Download work is I/O-bound (GIL released
on socket I/O), the heaviest CPU step (PDF/CBZ encode) is parent-side serial, and
bundle's heavy step is an EXTERNAL kcc-c2e subprocess (GIL released during
subprocess.run) -- so threads keep the real parallelism while gaining shared
memory (one browser session, persistent profile safe), shared logging config (no
spawn workaround), and a pinnable progress bar. A browser cannot cross a process
boundary, so "browser usable in a worker" REQUIRES threads + one shared,
lock-serialized session. Plan + status:
  - [x] (a) download path Pool -> ThreadPool; dropped configure_logging
    initializer + import. Bar pins (user-confirmed). Commit 95fbed2.
  - [x] (a') bundle.py Pool -> ThreadPool (kcc-c2e parallelism survives as
    subprocesses; bar pins; dropped initializer).
  - [x] (a'') removed the dead LOG_LEVEL_ENV worker-propagation write-path
    (cli() no longer sets os.environ[LOG_LEVEL_ENV]; dropped the now-unused
    os + LOG_LEVEL_ENV imports). Kept LOG_LEVEL_ENV as an external "set level via
    env" read feature in configure_logging; fixed the spawn-worker comments;
    test_cli now asserts the logger level only.
  - [x] (b) shared lock-serialized browser session: `_BrowserRuntime` owns one
    event loop on a daemon thread (Proactor on Windows) + one shared browser;
    every BrowserFetcher op routes through `_RUNTIME.submit(coro)` (marshalled
    onto the loop thread via run_coroutine_threadsafe, serialized by a lock) and
    uses `ensure_browser()` instead of per-call start/stop. Collapses N browsers
    -> 1 reusable session; the per-call asyncio.run() is gone (removes the
    Windows "I/O operation on closed pipe" shutdown noise); browser stopped once
    at interpreter exit (atexit) with a short post-stop sleep so transports close
    inside the live loop. capture_xhr now runs in its OWN tab (new_tab + close in
    finally) so its CDP handlers don't leak onto the shared browser. conftest
    guard repointed to `_BrowserRuntime._launch`. The loop-thread + serialization
    + lifecycle are unit-tested with plain coroutines (4 tests); the
    browser-launching methods are real-browser-only (# pragma: no cover).
    LIVE-VALIDATE on a real download: nodriver-on-Proactor-in-daemon-thread, the
    1-browser-reuse across search/chapters/reader, capture_xhr tab lifecycle, and
    that the shutdown noise is actually gone. UPDATE: validated LOCALLY against
    about:blank (tools probe, since deleted) -- the runtime launches and the
    `I/O operation on closed pipe` shutdown noise is GONE (vs the per-call
    asyncio.run pattern, which still shows it). Real-site reuse still to confirm.
  - [x] (b-fix1) live: the persistent browser kept raising itself over the CLI
    (old code called bring_to_front on every page load). Split `_focus_window`
    into `_enable_focus_emulation` (passive -- clears CF without stealing OS
    foreground, used always) and `_bring_to_front` (only when an interactive
    challenge needs solving). Normal runs no longer cover the menu. (Chrome may
    still grab focus ONCE at launch; alt-tab to the terminal.)
  - [x] (b-fix2) live: the "Downloading chapters 0/3" bar didn't advance though
    chapters finished -- `pool.imap` yields in submission order, so a slow first
    chapter (browser/lock) held the bar back. Switched to `imap_unordered` (order
    doesn't matter; parent assembles by id) + conftest patches imap_unordered.
  - [x] (b-fix3) live: `_looks_like_challenge` falsely flagged a normal MangaFire
    page (it carries Cloudflare's always-on challenge-platform/Turnstile SCRIPT),
    so `_wait_for_content` hung forever prompting "verify you are human" and kept
    the browser in front. Now matches ONLY real interstitial TEXT (just a moment
    / verify you are human / ...), never the always-on CF script -- regardless of
    page size. (The earlier size-gated attempt wasn't enough; MangaFire pages can
    be under the size hint.)
  - [x] (b-fix4) live: a stuck browser op couldn't be Ctrl-C'd -- the shared
    session blocked the main thread on `future.result()` with no timeout, which
    SIGINT can't interrupt on Windows. `submit` now polls `result(timeout=0.5)`
    in a loop and cancels the future on KeyboardInterrupt, so Ctrl-C breaks out.
  - [x] (b-followup) browser-vs-CLI focus: launch the headful window OFF-SCREEN
    (`--window-position=-32000,-32000` in _BROWSER_KWARGS) so it never covers the
    CLI menu/prompts. More reliable than CDP "minimized" (which was a no-op on
    Windows -- it only resized). Off-screen keeps the page VISIBLE to the renderer
    so scroll-driven lazy-load still works (unlike a minimized window). An
    INTERACTIVE challenge brings it on-screen + maximizes (`_show_browser_window`)
    + `_bring_to_front` so the user can solve it. Best-effort. LIVE-VALIDATE: that
    Chrome honors the off-screen position (some builds clamp window pos) and that
    the menu keeps focus. UPDATE: CONFIRMED LOCALLY (about:blank probe) -- Chrome
    honors the off-screen launch (window bounds came back left=-32000,top=-32000);
    minimize() was confirmed a no-op (reports MINIMIZED state but bounds reset to
    0,0,1280,720 -- the "resized not minimized" the user saw); maximize() works
    for restore. Menu-focus feel + kakalot scroll-while-offscreen still to confirm
    on a real run (off-screen keeps the page VISIBLE, so lazy-load should be ok).
  - [ ] (b-headless) **headless vs headful from the PROBE verdict** (user idea):
    the probe already classifies requests / nodriver-headless / nodriver-headful
    / nodriver-manual, but parsers all use a headful `BrowserFetcher()`. Thread
    the probe's per-source verdict into the parser/source so a source that only
    needs a headless browser runs headless (no window at all -> no focus issue,
    faster), reserving headful for the manual-solve sources. Needs: a per-source
    browser-mode setting (declared/derived from a probe) + BrowserFetcher honoring
    a headless flag (+ the shared session keyed by mode). Larger; design with the
    registry. Minimize-by-default already removes most of the UX pain, so this is
    an optimization, not urgent.
    - DECISION (analysis): **DEFER until a source actually needs it** -- building
      it now would be unused plumbing. Reasoning: (1) headless Chrome is more
      bot-detectable and typically FAILS Cloudflare, so the CF-gated browser
      sources (mangafire/mangago/kakalot) must stay headful; (2) the non-CF
      sources mostly use curl_cffi, not the browser; (3) with the single shared
      session, headless is a PROCESS-WIDE choice, not per-source. So no current
      source is a headless candidate -> a flag/attribute would sit unused. The
      probe's `nodriver-headless` verdict identifies the case (JS-rendered, no
      wall); add the per-source mode + a headless launch flag WHEN such a source
      appears, and validate it live then. (off-screen launch already fixed the
      focus UX this would otherwise help.)
  - [x] (c) opt-in persistent profile (C11(1)) -- `BROWSER_PROFILE_ENV`
    (`MANGASCRAPER_BROWSER_PROFILE`): when set, `_resolve_profile_dir` reuses
    that dir as nodriver's user_data_dir so a manually-solved challenge +
    cf_clearance survive across runs; unset = throwaway mkdtemp (today's
    behaviour). Safe given the single shared session (no SingletonLock clash).
    Path-resolution unit-tested (env-set reuses+creates dir; unset = fresh tmp);
    `_launch` (real browser) stays no-cover. README documents the env var.
    VALIDATED LOCALLY: two sequential runtime sessions reused the same persistent
    dir with NO SingletonLock collision (and no shutdown noise).
  - [x] FOLLOW-UP: bound total download concurrency -- DONE. `download_image`
    holds a process-wide `_DOWNLOAD_SEMAPHORE` (BoundedSemaphore) for the whole
    call, so the nested pools (jobs x cpu_count) can't exceed a hard cap of
    concurrent CDN requests. Default 8, overridable via
    `MANGASCRAPER_MAX_CONCURRENT_DOWNLOADS` (`_max_concurrent_downloads`, pure +
    unit-tested). Slot acquired AFTER the F4 ease-off wait (so a cooling-down
    thread doesn't hold a slot). README documents the env var.

## UX additions
- [x] **Stable chapter file index (de-dup fix)** — a chapter's saved filename
  used the position in the CURRENT selection (`enumerate(chapter_ids)`), so
  "download 700.6 alone" -> `..._chapter_1_700.6` but "download all" ->
  `..._chapter_<realpos>_700.6`; the already-on-disk check rebuilt the path from
  the run's index and never matched, so the same chapter re-downloaded under two
  names. Now the index is the chapter's STABLE 1-based position in the full
  series list (`MangaBuilder._chapter_order`, set in get_manga_chapters), so the
  name is identical regardless of selection and de-dup holds. Also stabilizes
  `Chapter.number` + bundle ordering. Regression test added. (Pre-existing bug.)
  NOTE: existing downloads with the old selection-based name get re-downloaded
  ONCE into the stable name, then are idempotent.
- [x] **End-of-run download summary** — `summarize_downloads()` (pure, tested)
  tallies the worker results into downloaded / already-present / incomplete /
  failed counts; `MangaBuilder._log_download_summary` prints it at the end of a
  run and warns with the specific chapter ids that are incomplete (missing
  pages) or failed (couldn't fetch), so they aren't lost in the scrolled-past
  per-chapter logs. Derived from the existing `ChapterDownload.complete`/`pages`
  signal -- no new plumbing.
- [x] **`--chapters` honored with `--search`** — DONE. `manga_search` gained a
  `preselected` arg; `cli()` passes the CLI `--chapters` into it (both the
  `--search` path and the not-found `--manga` fallback), so an explicit
  `--chapters` is used as-is and the interactive prompt is skipped. Prompt still
  appears when no `--chapters` was given. 2 tests (honors preselected / prompts
  when absent).

## Live-verified sources (user, on their laptop)
- **mangabuddy** (default source) — search + chapter listing + page-image
  download all work end-to-end. CONFIRMED.
- **mangafire** — NOT CONFIRMED since the October 2026 rebuild. The old
  parser worked end-to-end in June (C1 REALITY CHECK); the site then changed
  completely and the parser was rewritten (C12), which has not run live yet.
- **kakalot family** (manganelo/manganato/mangakaka) — search + chapter listing
  work; page-image download is documented-unsupported (interactive Turnstile +
  browser-only CDN, see C2-kakalot-images).
- **mangago** — fixed + fixture-backed this session (C2).

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
- [x] **R1 [STRUCT][P1] Two-tier test split (engine vs parser).** **DONE** (this
  session). Engine tests no longer run through real site parsers: `test_parsers.py`
  now parametrizes over a synthetic site-independent `EngineSiteParser` /
  `EngineMangaParser` (helpers.py) that exercises the base wiring + the
  fetch_soup → 404 → MangaDoesNotExist contract with no real markup ("if a site
  died tomorrow, the engine test still means something"). The
  `ALL_PARSERS = [MangaReader, MangaKaka, MangaFast]` real-site coupling is gone
  (those three were retired anyway). Parser-tier extraction tests now exist for
  every live site — mangabuddy, mangafire, mangago, and the kakalot family
  (manganelo/manganato/mangakaka via test_kakalot) — all fixture-backed from
  promoted probe captures. The suite is fully mocked (no live network), so no
  `integration`-tagged tests exist yet; the marker stays ready for any future
  genuinely-live test. Gates green, 366 pass.
  - SMELL (was): generic base-class behavior tested THROUGH dead site parsers.
  - NOTE: user agrees with this framing. NOT "add live sites to ALL_PARSERS" —
    that was the wrong original wording. STRUCT/larger; sequence with the Wave C
    parser work (probe → fixtures → tests) since the fixtures come from probing.
  - SOURCE OF FIXTURES: the probe already writes real responses to
    probe_out/<site>/ and the workflow promotes a curated subset to
    tests/test_files/<site>/. Those ARE the parser-test fixtures, and scaffold's
    generate_tests already emits the matching fixture-backed test. So each Wave C
    re-probe (C1 mangafire, C2 mangago/mangapark) naturally yields its parser
    test — R1's parser tier is a byproduct of probing, not separate work.
- [x] **R2 [QUICK][P3] `--upload mega` dead choice.** DONE: dropped `"mega"` from
  the `--upload` argparse choices (the user-facing bug — it advertised a mode
  that errored) and from the `services` dict in `__main__`. Swept the rest:
  removed the `mega.*` pyproject mypy override, the commented `MegaUploader`
  class + `from mega.mega import Mega` import in uploaders.py, the
  `MockedMega`/`MockedMegaNotFound` mocks in helpers.py, and the commented Mega
  tests/parametrize in test_uploaders.py. Gates green.
- [x] **R3 [QUICK][P2] `kcc-c2e` (and other) unchecked external deps** — DONE.
  - Audited all external-binary/subprocess use: `kcc-c2e` is the only subprocess
    call. Its PRESENCE check (RuntimeError when missing) was adequate, but the
    RESULT was ignored: `subprocess.run(command)` had no return-code check, so a
    kcc-c2e FAILURE (bad input, or its own kindlegen dep missing) silently
    produced a missing/partial MOBI. Extracted `Bundle._convert_to_mobi(cbz,
    mobi)` which now checks `returncode == 0` AND the output exists, raising with
    kcc-c2e's stderr tail otherwise (capture_output=True). 4 unit tests
    (missing / nonzero-exit / missing-output / success).
  - FOUND ELSEWHERE (the "kcc-c2e might not be the only one" hunch paid off):
    `base.create_page` used `ImageFont.truetype("arial.ttf", 20)` — arial.ttf is
    Windows-only, and create_page runs on the download-FAILURE path, so on
    Linux/macOS/CI a failed page made the error handler ITSELF raise OSError.
    Added `_placeholder_font(size)` that falls back to `ImageFont.load_default()`
    when arial is absent; 2 tests. Gates green, 363 pass.
- [x] **R4 [P3] Enable strict optional (drop `no_strict_optional`)** — DONE
  (partial, by design).
  - Exploratory `mypy --strict-optional` surfaced 25 errors in 10 files (the
    "many at once" the note predicted). Categorised + fixed the 7 files NOT in
    Wave C's territory: utils CustomAdapter.process (guard `self.extra`),
    fetchers RequestsFetcher cookies (str|None coerce) + capture_xhr assert on
    the captured url, kakalot `_scrape_volume`/`page_urls` dropped the spurious
    Optional (they raise, never return None) to match the base signature,
    mangafast two `e.response is not None` guards, uploaders BaseUploader
    `adapter` is now a non-Optional property over `_adapter` (raises if used
    before `_setup_adapter`) + `upload` returns `[]` not None, `__main__`
    download_manga `manga_title: Optional[str]`.
  - EXEMPTED (per-module `strict_optional = false` override): mangago, mangapark,
    mangareader — they're C2's fix-or-retire targets and carry pre-existing
    Optional violations; C2 removes the exemption when it reworks/retires them.
    This keeps strict optional ON for all core + kept-parser code now, without
    colliding with the in-flight C2 work.
  - Gates green, mypy clean (33 files), 363 pass.
- NOTE: `extract_chapter_number` is NOT orphan (review §3 P3 guessed unused) —
  it has parametrized tests in test_utils.py. Leave it; verify relevance only if
  touching that area.
- README `source = mangareader` (review-adjacent) — folded into A2's note.

Anything in code-review.md NOT listed here is either already fixed or a P3 nit
not worth a backlog slot.
