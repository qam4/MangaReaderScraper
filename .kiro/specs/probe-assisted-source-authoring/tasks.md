# Implementation Plan: Probe-assisted source authoring

## Overview

Three independently shippable phases extend the probe and add an authoring-time
generator. Phase 1 (recommendation synthesis) and Phase 2 (map-by-example) are
pure additions to `scraper/probe.py`; Phase 3 (parser scaffold) adds
`scraper/scaffold.py`. Gates after every task:
`uv run ruff check ./scraper ./tests`, `uv run ruff format`,
`uv run mypy scraper`, `uv run pytest`. Commit per task; push + watch CI per the
commit-checklist.

## Tasks

### Phase 1 — Recommendation synthesis

- [x] 1. Add `StagePlan` and `Recommendation` dataclasses (with `render()`) to
  `scraper/probe.py`.
  - _Requirements: 1.1, 1.5_
- [x] 2. Implement `synthesize_recommendation(ajax_urls, api_backends,
  api_bodies, page_html)` deciding `api_open`, `default_fetcher`, and the three
  stage plans from the artifacts.
  - _Requirements: 1.2, 1.3, 1.4_
- [x] 3. Unit-test the synthesizer: open-API site (mangak.io-like) → curl_cffi
  + embedded-images note; challenged-everywhere site → browser; no-API HTML
  site. Use crafted inputs mirroring real captures.
  - _Requirements: 1.6_
- [x] 4. Wire `_probe` to build the api_backends map + parsed bodies in-memory
  and write `recommendation.txt`; update the module docstring and the final
  "Done" hint. Update `docs/adding-a-source.md` to list `recommendation.txt`.
  - _Requirements: 1.1, 7.2_

### Phase 2 — Map-by-example (Step A)

- [x] 5. Implement `json_paths_for_value(obj, value)` with the four match kinds
  (exact, substring, numeric-equiv, path-prefix) and bounded-depth traversal;
  add `PathMatch`.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_
- [x] 6. Unit-test the locator against `search_naruto.json` /
  `chapters_naruto.json`: finds `name`/`slug`, substring `700.5` in a chapter
  name, numeric/string equivalence, `/naruto` path-prefix, and unresolved
  values.
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
- [x] 7. Implement `sibling_mismatch_check(obj, match, value)` +
  `SiblingWarning`; flag numeric / `*number*` / `*count*` / `*id*` siblings that
  disagree.
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
- [x] 8. Unit-test the gotcha checker on the mangak.io case (`name="Chapter
  700.5"` vs sibling `chapter_number=748`) and the no-warning case.
  - _Requirements: 3.5_
- [x] 9. Implement `build_field_map(captures, examples)` (search standalone
  `api_*.json` + embedded `__NEXT_DATA__` via `_next_data_from_html`) and
  `render_field_map(entries)`; add `FieldMapEntry`.
  - _Requirements: 4.2, 4.3, 4.4_
- [x] 10. Add `--map-by-example name=value ...` to `main()`; collect parsed
  captures and write `field_map.txt`. Unit-test CLI parsing + report rendering
  against the mangabuddy fixtures.
  - _Requirements: 4.1, 4.5_
- [x] 11. Document `--map-by-example` in `docs/adding-a-source.md` (a JSON
  counterpart to `--find`).
  - _Requirements: 7.2_

### Phase 3 — Parser scaffold (Step B). Build only after Phase 2 has been used on a real site other than mangak.io.

- [~] 12. Define `ParserConfig` + `SearchSpec`/`ChaptersSpec`/`ImagesSpec` and
  `load_parser_config(text)` (toml parse + validation with `ConfigError`) in a
  new `scraper/scaffold.py`. Add `get_by_path(obj, path)` helper.
  - _Requirements: 5.1, 5.2, 5.3, 5.4_
- [~] 13. Unit-test config load/validate, including a full mangak.io
  `parser.toml` and a missing-field error case.
  - _Requirements: 5.5_
- [~] 14. Implement `generate_parser(cfg)` emitting the three parser classes for
  the `api` and `next_data` image modes, wired to the registry and shared
  building blocks, with explicit hooks for underivable transforms.
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.6_
- [~] 15. Implement `generate_tests(cfg, fixtures)` emitting fixture-backed
  tests.
  - _Requirements: 6.1, 6.5_
- [~] 16. Golden + round-trip tests: assert emitted source contains the right
  endpoints/paths/fetcher; generate the mangak.io parser to a temp module,
  import it, and run mangabuddy-equivalent assertions against the fixtures.
  - _Requirements: 6.5_
- [~] 17. Add a CLI entry to generate from a config (e.g. `python -m
  scraper.scaffold <parser.toml>`), and document the Step B workflow + config
  schema in `docs/adding-a-source.md`.
  - _Requirements: 6.1, 7.2_
- [~] 18. (`html` image mode) Extend the generator for the plain-HTML image mode
  with a configured selector, if a real site needs it. Defer until then.
  - _Requirements: 6.2_

### Cross-cutting

- [x] 19. Verify no phase adds site hammering; ensure all new logic is pure +
  unit-tested with no network/browser; confirm `probe_out/` stays gitignored.
  - _Requirements: 7.1, 7.3, 7.4_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": [1, 5], "reason": "Phase entry points; no deps. 1=recommendation dataclasses, 5=JSON locator (independent phases, parallelizable)." },
    { "wave": 2, "tasks": [2, 6, 7], "reason": "2 needs 1; 6 and 7 need the locator from 5." },
    { "wave": 3, "tasks": [3, 4, 8, 9], "reason": "3/4 need synthesizer 2; 8 needs checker 7; 9 needs locator 5 (+6)." },
    { "wave": 4, "tasks": [10], "reason": "map-by-example CLI needs build_field_map (9)." },
    { "wave": 5, "tasks": [11], "reason": "docs for --map-by-example after it exists (10)." },
    { "wave": 6, "tasks": [12], "reason": "Phase 3 start: config schema/loader (after Phase 2 validated on a real site)." },
    { "wave": 7, "tasks": [13, 14], "reason": "13 tests loader 12; 14 generates parser from config 12." },
    { "wave": 8, "tasks": [15], "reason": "test generator needs parser generator 14." },
    { "wave": 9, "tasks": [16], "reason": "golden/round-trip needs both generators 14, 15." },
    { "wave": 10, "tasks": [17], "reason": "scaffold CLI + docs after generation works (16)." },
    { "wave": 11, "tasks": [18], "reason": "html image mode, deferred until a real site needs it." },
    { "wave": 12, "tasks": [19], "reason": "cross-cutting verification runs after the phases land." }
  ]
}
```

Phases 1 and 2 can proceed in either order or in parallel. Phase 3 has no code
dependency on Phases 1–2 but should follow Phase 2 in practice (the human uses
`field_map.txt` to author the config).

## Notes

- Build Phase 2 first if choosing one: it directly mechanizes the manual field
  identification + gotcha spotting that is the feature's core value.
- Do not start Phase 3 until `--map-by-example` has been used on a real site
  other than mangak.io, so the generator is validated beyond its design example.
- Reuse existing pure-helper test style (`tests/test_probe.py`) and the shipped
  `tests/test_files/mangabuddy/*` fixtures as realistic inputs.
- Keep browser/network at the edges; everything analytical stays pure and
  mock-/fixture-tested, honestly flagged as not live-verified where relevant.
