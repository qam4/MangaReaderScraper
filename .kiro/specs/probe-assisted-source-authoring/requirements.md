# Requirements Document

## Introduction

Adding a manga source has an irreducible manual core (refactoring-plan
"Problem A"): a human must look at a real site and decide which JSON field is the
title, which is the chapter slug, where the page images live, and what gotchas
lurk (e.g. a `chapter_number` that is a sequence counter, not the displayed
number). The probe already makes the *looking* cheap — it captures API response
bodies and reports backend reachability. What is still fully manual is the chain
from those captures to a working parser:

1. synthesizing the captures into a recommended approach,
2. mapping captured JSON onto the parser's data contract,
3. scaffolding the parser and its tests.

This feature turns that chain into an assisted, end-to-end workflow. The guiding
principle is honest assistance: **the human supplies ground truth** (values they
can see on the page); the tooling does the tedious locating, consistency
checking, and boilerplate — it never guesses a schema from nothing or emits
confident-looking stubs.

The work is staged so each phase delivers value alone:
- **Phase 1 — Recommendation synthesis** (the "suggest the best way" summary).
- **Phase 2 — Map-by-example** (Step A: JSON `--find` + gotcha detection).
- **Phase 3 — Parser scaffold** (Step B: generate a parser + tests from a
  human-confirmed field map).

## Glossary

- **Capture artifacts**: files the probe writes to `probe_out/<site>/`
  (`ajax_log.txt`, `api_index.txt`, `api_*.json`, `api_backends.txt`,
  `fetch_recommendation.txt`, `candidates.txt`, `page.html`).
- **Field map**: the correspondence between a parser's needed data (title, slug,
  chapter number, image list) and concrete JSON paths in the captures.
- **JSON path**: a dotted/indexed locator such as `data.items[0].name`.
- **Embedded JSON**: a JSON payload inside a page's HTML (e.g. Next.js
  `__NEXT_DATA__`), as opposed to a standalone API response body.

---

## Requirements

## Requirement 1 — Recommendation synthesis (Phase 1)

**User story:** As a developer adding a source, I want the probe to read its own
capture artifacts and print a single recommended approach, so that I don't have
to cross-read five files to decide the fetcher and the per-stage strategy.

### Acceptance criteria

1. WHEN the probe finishes capturing a site THEN it SHALL write a
   `recommendation.txt` summarizing: the host(s) seen, whether an open JSON API
   was found, the recommended fetcher, and the candidate search / chapter-list /
   image mechanisms.
2. WHEN `api_backends.txt` shows curl_cffi returns JSON for an endpoint THEN the
   recommendation SHALL prefer `CurlCffiFetcher` (no browser) for that endpoint.
3. WHEN plain requests and curl_cffi are both challenged but the browser
   captured a body THEN the recommendation SHALL state that `BrowserFetcher` is
   required for that stage.
4. WHEN the captures contain no image-list endpoint but a page payload contains
   an images array THEN the recommendation SHALL note that images are embedded
   (page/`__NEXT_DATA__`), not API-served.
5. The recommendation SHALL be advisory: it SHALL phrase conclusions as
   suggestions for the developer to confirm, not as decisions.
6. The synthesis logic SHALL be implemented as pure functions over the artifact
   contents (or in-memory equivalents) and SHALL be unit-tested without a
   browser or network.

## Requirement 2 — Locate values in captured JSON (Phase 2, Step A core)

**User story:** As a developer, I want to give the probe values I can see on the
page and have it tell me where they live in the captured JSON, so that I can
identify the title/slug/chapter fields without scrolling thousands of lines.

### Acceptance criteria

1. WHEN given a parsed JSON structure and a target value THEN the locator SHALL
   return every path whose leaf equals the value.
2. WHEN a leaf is a string that contains the target value as a substring THEN
   the locator SHALL include that path, marked as a substring (not exact) match
   (e.g. `"700.5"` within `"Chapter 700.5"`).
3. WHEN a leaf is numeric and the target is the equivalent string (or vice
   versa) THEN the locator SHALL treat them as matching (e.g. `748` vs `"748"`).
4. WHEN a target value matches a URL-ish leaf differing only by a leading path
   separator THEN the locator SHALL include it, noting the prefix (e.g. `naruto`
   vs `/naruto`).
5. WHEN no path matches a target THEN it SHALL be reported as unresolved rather
   than omitted silently.
6. The locator SHALL traverse nested objects and arrays to a bounded depth and
   SHALL not raise on cyclic-free arbitrary JSON.

## Requirement 3 — Flag sibling-field gotchas (Phase 2, Step A heuristic)

**User story:** As a developer, I want the tool to warn me when a field adjacent
to my matched value looks like it should hold the same datum but disagrees, so
that gotchas like sequence-counter chapter numbers are surfaced automatically.

### Acceptance criteria

1. WHEN a target value is located at a path THEN the checker SHALL inspect
   sibling keys within the same object.
2. WHEN the target value contains a number AND a sibling key suggests it should
   hold that number (e.g. a `*number*`, `*count*`, or `*id*` field, or any
   numeric sibling) AND the sibling's value differs THEN the checker SHALL emit a
   warning identifying the sibling path and both values.
3. A warning SHALL be phrased as a hint (the tool does not assert the sibling is
   wrong), and SHALL suggest the likely interpretation when one is obvious (e.g.
   "likely a sequence counter; derive the number from the matched field").
4. WHEN no sibling mismatch is found THEN no warning SHALL be emitted (no noise).
5. The checker SHALL be pure and unit-tested, including the mangak.io case
   (`name="Chapter 700.5"` vs sibling `chapter_number=748`).

## Requirement 4 — Map-by-example CLI and report (Phase 2, Step A surface)

**User story:** As a developer, I want a single probe command that takes my known
values and writes a readable field-map report across all captured JSON, so that
the locating and gotcha-checking are one step.

### Acceptance criteria

1. The probe SHALL accept a `--map-by-example` option taking one or more
   `name=value` pairs.
2. WHEN run THEN it SHALL search every captured JSON body — both standalone
   `api_*.json` and embedded `__NEXT_DATA__` extracted from `page.html` — for
   each value.
3. WHEN run THEN it SHALL write a `field_map.txt` listing, per input value, the
   matching path(s), the source file, any sibling-mismatch warnings, and an
   "unresolved" section for values not found.
4. WHEN multiple paths match one value THEN all SHALL be listed (the tool does
   not pick "the" field); the developer interprets.
5. The CLI parsing and report rendering SHALL be covered by tests using the
   existing `tests/test_files/mangabuddy/*.json` fixtures as input.

## Requirement 5 — Confirmed field-map config (Phase 3, Step B input)

**User story:** As a developer, I want to record the field map I confirmed into a
small declarative config, so that scaffolding has a verified, human-owned source
of truth rather than a guess.

### Acceptance criteria

1. The feature SHALL define a documented config schema covering: site name,
   registry name, base/API URLs, chosen fetcher, and the search / chapters /
   images sections with their endpoints and field paths.
2. The config SHALL support the three observed image-source modes: a standalone
   API endpoint, embedded page JSON (`next_data`), and HTML elements.
3. The config SHALL support expressing slug→id resolution via the search step
   (the mangak.io case), including which fields supply the id and cv.
4. WHEN a config omits a required field THEN the loader SHALL report a clear
   validation error naming the missing field.
5. Config loading and validation SHALL be unit-tested, including a complete
   mangak.io config that round-trips to the values the live parser uses.

## Requirement 6 — Parser + test scaffold generation (Phase 3, Step B output)

**User story:** As a developer, I want to generate a runnable parser module and
fixture-backed test file from a confirmed config, so that the mechanizable 80% of
a new parser is written for me while site-specific quirks stay in real code.

### Acceptance criteria

1. WHEN given a valid config and the captured fixtures THEN the generator SHALL
   produce a `scraper/parsers/<site>.py` implementing the three parser classes
   wired to the registry, and a `tests/test_<site>.py` exercising them against
   the fixtures.
2. The generated parser SHALL use the shared building blocks (`CurlCffiFetcher`
   / `BrowserFetcher`, `sort_chapter_ids`, `SearchResult`, chapter-number-from-
   name parsing) — never a direct HTTP/browser library call.
3. WHEN the config marks an image source as embedded `next_data` THEN the
   generated `page_urls` SHALL fetch the page (curl_cffi first, browser
   fallback) and read the configured images path, mirroring the shipped
   mangabuddy parser.
4. The generator SHALL emit explicit, clearly-marked hooks (not fake
   implementations) for site-specific transforms it cannot derive (e.g.
   descramble, vrf tokens).
5. WHEN the generated tests are run THEN they SHALL pass against the captured
   fixtures, and a wrong/missing field path SHALL cause a test failure rather
   than a silent mis-parse.
6. The generator SHALL produce a .py skeleton (thrown-away config as input), NOT
   a runtime config-interpreting parser, so that quirks live in maintainable
   code.

## Requirement 7 — Workflow safety and honesty (cross-cutting)

**User story:** As the maintainer, I want the whole assisted workflow to stay
within the project's "probe and be realistic" ethos, so that it never encourages
aggressive scraping or presents unverified output as fact.

### Acceptance criteria

1. No phase SHALL add automated retry/hammering of a site beyond a single
   capture pass; live verification remains a manual developer step.
2. Recommendation and map output SHALL be labeled advisory; generated parsers
   SHALL be labeled as scaffolds requiring live verification.
3. All new analysis/synthesis/generation logic SHALL be pure and unit-tested
   without network or browser; browser/network paths SHALL remain mock-tested
   and honestly flagged as not live-verified.
4. Probe scratch output SHALL remain under the gitignored `probe_out/`; only
   curated fixtures are promoted to `tests/test_files/<site>/` by hand.
