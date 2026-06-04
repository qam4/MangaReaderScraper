# Design — Probe-assisted source authoring

## Overview

This feature extends `scraper/probe.py` and adds a small generator so the path
from "probe a site" to "working parser" is assisted end to end, in three phases
that each ship independently:

- **Phase 1 — Recommendation synthesis**: pure functions that read the existing
  capture artifacts and emit `recommendation.txt`.
- **Phase 2 — Map-by-example (Step A)**: a JSON locator + sibling-mismatch
  checker, surfaced as `--map-by-example`, writing `field_map.txt`.
- **Phase 3 — Parser scaffold (Step B)**: a config schema + a code generator
  that emits `scraper/parsers/<site>.py` and `tests/test_<site>.py`.

The design keeps every analytical/generative piece **pure and unit-testable**,
consistent with how `analyze_html`, `compare_fetches`, `find_text`,
`is_api_like_url`, etc. are already built and tested. Browser/network stays at
the edges and mock-tested.

## Architecture

```
                 probe capture (existing)
                         │
        ┌────────────────┼─────────────────────┐
        ▼                ▼                       ▼
 ajax_log / api_*   api_backends         page.html / __NEXT_DATA__
        │                │                       │
        └──────► Phase 1: synthesize_recommendation() ──► recommendation.txt
                         │
   developer supplies known values (--map-by-example)
                         ▼
        Phase 2: json_paths_for_value() + sibling_mismatch_check()
                         │
                         ▼  developer reads, confirms
                  field_map.txt
                         │
   developer writes parser.toml (confirmed map)  ◄── informed by field_map.txt
                         ▼
        Phase 3: load_parser_config() ─► generate_parser() / generate_tests()
                         │
                         ▼
        scraper/parsers/<site>.py  +  tests/test_<site>.py
```

All three phases live alongside the existing pure probe helpers; Phase 3's
generator is a separate module (`scraper/scaffold.py`) since it is authoring-time
tooling, not runtime scraping.

## Components and interfaces

### Phase 1 — recommendation synthesis

New pure functions in `scraper/probe.py`:

```python
@dataclass
class StagePlan:
    name: str                 # "search" | "chapters" | "images"
    mechanism: str            # human text, e.g. "api.mangak.io/titles/search"
    fetcher: str              # "curl_cffi" | "browser" | "requests" | "unknown"
    note: str = ""            # gotcha hints / embedded-vs-api

@dataclass
class Recommendation:
    hosts: list[str]
    api_open: bool
    default_fetcher: str
    stages: list[StagePlan]
    def render(self) -> str: ...

def synthesize_recommendation(
    ajax_urls: list[str],
    api_backends: dict[str, str],   # url -> "curl_cffi" | "requests" | "blocked"
    api_bodies: list[tuple[str, str]],
    page_html: str | None,
) -> Recommendation: ...
```

Decision logic (all derivable from artifacts already captured):
- **api_open**: any data endpoint where `api_backends` says curl_cffi/requests
  got JSON.
- **default_fetcher**: cheapest backend that worked across the data endpoints
  (requests < curl_cffi < browser).
- **search/chapters mechanism**: pattern-match captured URLs (`*search*`,
  `*chapter*`/`*titles*`) and pair with their `api_backends` verdict.
- **images mechanism**: if a data endpoint returns an array of image-looking
  URLs → API; else if a page/`__NEXT_DATA__` payload has an `images` array →
  embedded; else unknown.

`_probe` calls `synthesize_recommendation(...)` after the other writes and emits
`recommendation.txt`.

### Phase 2 — map-by-example (Step A)

New pure functions in `scraper/probe.py`:

```python
@dataclass
class PathMatch:
    path: str          # "data.items[0].name"
    leaf: object       # the matched leaf value
    kind: str          # "exact" | "substring" | "numeric" | "path-prefix"

def json_paths_for_value(obj, value: str) -> list[PathMatch]: ...

@dataclass
class SiblingWarning:
    sibling_path: str
    sibling_value: object
    message: str

def sibling_mismatch_check(obj, match: PathMatch, value: str) -> list[SiblingWarning]: ...

@dataclass
class FieldMapEntry:
    name: str                       # the label the dev gave ("title")
    value: str
    matches: list[tuple[str, PathMatch]]   # (source_file, match)
    warnings: list[SiblingWarning]

def build_field_map(
    captures: dict[str, object],    # source_file -> parsed json
    examples: dict[str, str],       # name -> value
) -> list[FieldMapEntry]: ...

def render_field_map(entries: list[FieldMapEntry]) -> str: ...
```

- `json_paths_for_value` walks dicts/lists recursively, building a dotted/indexed
  path, applying the four match kinds (exact, substring, numeric-equiv,
  path-prefix) with bounded depth.
- `sibling_mismatch_check` looks at keys in the matched leaf's parent object;
  flags numeric / `*number*` / `*count*` / `*id*` siblings whose value differs
  from a number contained in the target.
- `build_field_map` runs the locator over every capture (standalone `api_*.json`
  + embedded `__NEXT_DATA__` via the existing `_next_data_from_html`).

CLI: add `--map-by-example name=value ...` to `main()`; `_probe` (or a separate
artifact-only path) collects the parsed captures and writes `field_map.txt`.

### Phase 3 — parser scaffold (Step B)

New module `scraper/scaffold.py` (authoring-time, imported only by the CLI path
that generates):

```python
@dataclass
class SearchSpec:   endpoint: str; items: str; title: str; slug: str; ...
@dataclass
class ChaptersSpec: endpoint: str; list: str; chapter_name: str; chapter_slug: str; id_from: str; cv_from: str
@dataclass
class ImagesSpec:   source: str; page_url: str; images_path: str  # source: api|next_data|html
@dataclass
class ParserConfig:
    site: str; register_as: str; base_url: str; api_url: str; fetcher: str
    search: SearchSpec; chapters: ChaptersSpec; images: ImagesSpec

def load_parser_config(text: str) -> ParserConfig: ...        # parse + validate toml
def generate_parser(cfg: ParserConfig) -> str: ...            # returns .py source
def generate_tests(cfg: ParserConfig, fixtures: dict) -> str: ...
```

- **Templating**: plain string templates (or `string.Template`) over the
  `ParserConfig`. The generated module is structurally today's `mangabuddy.py`
  with field paths and endpoints substituted. No runtime config interpreter
  (decided: quirks belong in generated, editable code).
- **Image modes**: `api` → curl_cffi GET + json path; `next_data` → page fetch
  (curl-first/browser-fallback) + `_next_data_from_html` + path; `html` →
  `fetch_soup` + selector.
- **Hooks**: where a transform can't be derived (descramble, vrf), emit a
  clearly-named method raising `NotImplementedError("site-specific: …")` with a
  comment, never a fake body.
- **Path access**: a tiny `get_by_path(obj, "data.items[0].name")` helper
  (shared with Phase 2's locator inverse) resolves config paths at runtime in
  the generated code.

`load_parser_config` and `generate_*` are pure (string in/out), so generation is
unit-tested by asserting the emitted source contains the right endpoints/paths
and — stronger — by writing it to a temp module, importing it, and running it
against the fixtures.

## Data models

- **Recommendation / StagePlan** — Phase 1 output (above).
- **PathMatch / SiblingWarning / FieldMapEntry** — Phase 2 (above).
- **ParserConfig + sub-specs** — Phase 3 (above).

All are frozen-ish dataclasses with a `render()` where they produce text, exactly
like the existing `ProbeReport` / `FetchComparison`.

## Error handling

- Phase 1/2 operate on possibly-partial captures: missing files → treated as
  empty, never raise. Malformed JSON in a capture → skipped with a noted entry.
- Phase 2: a value found nowhere → "unresolved" section (Req 2.5), not an error.
- Phase 3: `load_parser_config` raises a `ConfigError` naming the missing/invalid
  field (Req 5.4). Generation never silently emits a stub for a required-but-
  unknown transform — it emits an explicit hook.

## Testing strategy

- **Pure unit tests** for every function above, mirroring `tests/test_probe.py`
  style, using the existing `tests/test_files/mangabuddy/*.json` (+ the
  `chapter_page.html`) as realistic inputs.
- **Golden tests** for the generator: assert the emitted parser source contains
  the configured endpoints/paths and the right fetcher calls.
- **Round-trip test** (strongest): generate the mangak.io parser from a
  `parser.toml` into a temp file, import it, and run the same assertions the
  hand-written `test_mangabuddy.py` makes against the fixtures — proving the
  generator reproduces the working parser.
- No network/browser in any test; CLI wiring tested by calling `main()` with
  args against fixture captures.

## Correctness Properties

Invariants the implementation must uphold (and that tests assert):

### Property 1: Locator soundness

Every `PathMatch` returned by `json_paths_for_value` resolves back to the given
value via `get_by_path` (the path is real and points at the matched leaf).

**Validates: Requirements 2.1, 2.6**

### Property 2: Locator completeness for exact matches

No exact-equal leaf anywhere in the structure is omitted from the results.

**Validates: Requirements 2.1**

### Property 3: No silent loss

Every input example appears in the field map either with matches or in the
"unresolved" section (Req 2.5).

**Validates: Requirements 2.5, 4.3**

### Property 4: Warning conservatism

A `SiblingWarning` is emitted only when a sibling both *looks*
numeric/identifier-like and *disagrees*; matching siblings never warn (Req 3.4),
so the absence of warnings is meaningful.

**Validates: Requirements 3.2, 3.4**

### Property 5: Generator faithfulness

For a valid config, the generated parser, when imported and run against the
fixtures, produces the same search/chapter/page results a correct hand-written
parser would (verified by the round-trip test, Req 6.5).

**Validates: Requirements 6.1, 6.5**

### Property 6: Advisory-only output

Recommendation and field-map text never assert a single answer when the evidence
is ambiguous; multiple matches are all listed (Req 4.4) and conclusions are
phrased as suggestions (Req 1.5).

**Validates: Requirements 1.5, 4.4**

### Property 7: Purity

Synthesis, locating, checking, config-loading, and generation are deterministic
functions of their inputs with no network/browser/file side effects (files are
written only by the thin CLI wrappers).

**Validates: Requirements 1.6, 7.3**

## Sequencing rationale

Phase 1 is independent and immediately useful. Phase 2 is the high-value core and
depends on nothing but the captures. Phase 3 depends on Phase 2 in *workflow*
(the human uses `field_map.txt` to write the config) but not in *code*; it is
built last and only after Phase 2 has been used on a real site other than
mangak.io, so the generator is validated against more than its design example.
