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

- [ ] **A7 [STRUCT-lite, LOW-PRI] Actually pin the progress bar across workers**
  - The bar can only be pinned if the parent controls ALL terminal writes during
    the download. Options (pick when scheduled):
    1. **Logging queue**: workers use a `QueueHandler` (logging only to a
       multiprocessing Queue); the parent runs a `QueueListener` whose single
       RichHandler renders through the Progress's Console. Correct + keeps logs
       visible AND bar pinned, but real spawn-safe multiprocess plumbing.
    2. **Quiet workers during download** (simplest, robust): workers log only
       WARNING+ during the parallel phase, so the parent's bar is essentially the
       only thing drawing. Lose per-volume INFO lines in the terminal (errors
       still show). Best effort:payoff.
    3. Accept it / revert to classic tqdm (degrades more gracefully under foreign
       writes — repaints at the bottom each tick — but never perfectly pinned).
  - DONE-WHEN: in a real terminal, a real multiprocess download keeps the bar at
    the bottom. Live-only to validate (mocked suite can't see it).
  - PRIORITY: low — cosmetic; the download works fine, the bar just scrolls.

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

- [~] **C10 [STRUCT] Probe: recommend the CHEAPEST working fetcher per stage
  (uniform fetcher-ladder reachability)** — IMAGE STAGE **DONE** (this session);
  page/API stages partially covered already; full uniformity is the remaining
  extension. Delivered: `candidate_image_urls` (page-image cluster, src OR
  data-src, skips data: placeholders + nav imgs) + `check_image_ladder`
  (requests → curl_cffi → cloudscraper, carrying page Referer + the live session
  cookies read via CDP) reporting the cheapest tier that returns a real image —
  replacing the old bare-requests-on-one-data-src check. `cheapest_working` +
  url extraction are pure/fixture-tested; the per-tier GET is injected (mocked
  in tests, real backends live). REMAINING (optional extension): rewire the
  page check (`compare_fetches`, today requests-vs-browser) and API check
  (`_check_api_backends`, today requests+curl_cffi) onto the same `FETCHER_LADDER`
  primitives so cloudscraper is tried everywhere and all three stages report via
  one uniform mechanism. — the bigger gap behind it. The probe is good at finding
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

- [ ] **C11 [STRUCT] Manual captcha-solve + persistent browser session** — the
  honest answer to "what do we do at a hard wall?" Today the wall ladder is
  curl_cffi (TLS impersonation) → cloudscraper (JS IUAM) → headful `nodriver`
  AUTO-clearance, and the cleared session's cookies CAN be harvested
  (`capture_xhr(with_cookies=True)` reads them via CDP) and reused by cheap
  fetchers (`download_image(cookies=...)` / curl_cffi) — that's how MangaFire
  avoids a browser per image. What's MISSING is human-in-the-loop solve:
    * `BrowserFetcher` is headful but never DETECTS a remaining challenge,
      PAUSES for the user to solve it, then resumes — it relies on auto-clear
      within a fixed `wait`. The probe even names a `nodriver-manual` strategy
      ("user solves it in the window") that no fetcher implements.
    * each `BrowserFetcher` call spins a FRESH throwaway profile
      (`mkdtemp` per call) and `browser.stop()`s in `finally`, so a
      manually-cleared session does NOT persist across calls; only explicitly
      harvested cookies carry forward. No reused `user_data_dir`.
  - WHY (user): curl_cffi / headless often dodge captchas, but some sites are
    genuinely captcha-protected; a manual-solve-once-then-continue path is a
    useful escape hatch even if rarely hit.
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

- [ ] **C2 [PROBE] Re-probe + fix-or-retire mangago / mangapark** (your live runs)
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

- [ ] **D3 [DECISION] Project rename** — `MangaReaderScraper` names a dead site.
  Package (`scraper/`) already neutral; only project/repo/dist/pyproject name +
  README stale. Friction: repo rename breaks clone URLs/CI/install. Only worth it
  bundled with the Wave B rebuild; standalone it's just paint (A2 is the real fix).

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

- [ ] **F4 [STRUCT, LOW-PRI] Respectful adaptive throttling on failure**
  - VALUE (user): the multiprocessing is to be fast on the happy path, but when a
    site is FAILING we should slow down, not keep fanning out at full concurrency.
    Today F1 only backs off a SINGLE failed request in its own worker; the other
    workers keep going full tilt. There's no global "the site is unhappy, everyone
    ease off."
  - IDEAS: (a) treat 429/503 specially — wait notably longer than a generic blip,
    and honor a `Retry-After` header when present (the site is telling us how long
    to wait); (b) a shared/global rate limiter or concurrency reducer across
    workers when rate-limit signals appear (needs cross-process coordination, same
    plumbing family as A7's logging queue).
  - DONE-WHEN: repeated rate-limit responses reduce overall request rate (not just
    per-request retry delay); Retry-After respected. Live to validate.
  - PRIORITY: low — F1's backoff is the cheap floor; this is the principled
    version. Sequence deliberately.

---

## Suggested order
A (all) → B (B1+B2 designed together, B2 test first) → C (probe-gated) →
D (fold D3 into B if rebuilding). Start nibbling at Wave A.

## Done this session (for reference)
- Probe-assisted source authoring spec: all 19 tasks (Phases 1-3 + html image
  mode), committed + pushed + CI green.
- A2 (stale default → mangabuddy) + A5 (total_pages count) — commit 864c363.

## Live-verified sources (user, on their laptop)
- **mangabuddy** (default source) — search + chapter listing + page-image
  download all work end-to-end. CONFIRMED.
- **mangafire** — works end-to-end with no manual captcha (see C1 REALITY
  CHECK). CONFIRMED. (Only the search path C7 + descramble C8 remain as
  low-pri unexercised edge cases, not breakage.)
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
