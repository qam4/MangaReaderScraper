"""
Tests for the probe's pure analysis helpers (no browser needed).
"""

import json
from pathlib import Path

import pytest

from scraper.probe import (
    FieldMapEntry,
    PathMatch,
    ProbeReport,
    Recommendation,
    SiblingWarning,
    StagePlan,
    _element_selector,
    analyze_html,
    api_dump_filename,
    build_field_map,
    compare_fetches,
    detect_challenge,
    find_text,
    get_by_path,
    is_api_like_url,
    is_json_mime,
    json_paths_for_value,
    load_captures,
    looks_like_json,
    main,
    parse_examples,
    render_field_map,
    render_matches,
    sibling_mismatch_check,
    site_name_from_url,
    summarize_backend_probe,
    synthesize_recommendation,
    write_field_map,
)

# ========================= site_name_from_url ============================


def test_site_name_strips_www_and_tld():
    assert site_name_from_url("https://www.mangabuddy.com/x") == "mangabuddy"
    assert site_name_from_url("https://mangafire.to/home") == "mangafire"
    assert site_name_from_url("https://natomanga.com/manga-x") == "natomanga"


def test_site_name_handles_port():
    assert site_name_from_url("http://localhost:8080/x") == "localhost"


# ============================ analyze_html ===============================


def test_analyze_finds_chapter_links():
    html = """
    <html><body>
      <a href="/read/foo/en/chapter-28.22">ch</a>
      <a href="/manga/foo/chapter_55">ch</a>
      <a href="/about">not a chapter</a>
      <a href="/read/foo/en/chapter-28.22">dup</a>
    </body></html>
    """
    report = analyze_html(html)
    assert "/read/foo/en/chapter-28.22" in report.chapter_links
    assert "/manga/foo/chapter_55" in report.chapter_links
    assert "/about" not in report.chapter_links
    # de-duplicated
    assert report.chapter_links.count("/read/foo/en/chapter-28.22") == 1


def test_analyze_finds_data_number_and_data_src():
    html = """
    <html><body>
      <a data-number="81" href="/x">81</a>
      <img data-src="https://cdn/p1.jpg">
      <img data-src="https://cdn/p2.jpg">
    </body></html>
    """
    report = analyze_html(html)
    assert len(report.data_number_samples) == 1
    assert "https://cdn/p1.jpg" in report.data_src_samples
    assert "https://cdn/p2.jpg" in report.data_src_samples


def test_analyze_finds_largest_img_cluster():
    html = """
    <html><body>
      <div class="header"><img src="logo.png"></div>
      <div class="reader">
        <img src="1.jpg"><img src="2.jpg"><img src="3.jpg">
      </div>
    </body></html>
    """
    report = analyze_html(html)
    assert report.largest_img_container == "div"
    assert report.img_container_count == 3


def test_analyze_empty_html_is_safe():
    report = analyze_html("<html></html>")
    assert report.chapter_links == []
    assert report.largest_img_container is None


def test_report_render_is_readable():
    report = ProbeReport(chapter_links=["/chapter-1"], img_container_count=0)
    text = report.render()
    assert "Candidate selectors" in text
    assert "/chapter-1" in text


# ========================= detect_challenge ==============================


def test_detect_challenge_flags_strong_markers_any_size():
    # strong interstitial phrases count regardless of page size
    big = "<title>Just a moment...</title>" + "x" * 200_000
    assert detect_challenge(big)
    assert detect_challenge("please enable javascript and cookies to continue")
    assert detect_challenge("Verify you are human")


def test_weak_markers_flag_only_on_small_pages():
    # CF infra on a TINY page -> likely a wall
    assert detect_challenge("<script src='/cdn-cgi/challenge-platform'></script>")
    # same infra on a LARGE page -> real content behind CF, NOT a block
    big = "<script src='/cdn-cgi/challenge-platform'></script>" + "x" * 200_000
    assert detect_challenge(big) == []


def test_detect_challenge_clean_page():
    assert detect_challenge("<html><body><h1>Dragon Ball</h1></body></html>") == []


def test_cloudflare_infrastructure_is_informational():
    from scraper.probe import cloudflare_infrastructure

    big = "<script src='/cdn-cgi/challenge-platform'></script>" + "x" * 200_000
    # not a challenge, but we still surface that the site is behind CF
    assert detect_challenge(big) == []
    assert cloudflare_infrastructure(big)


def test_analyze_flags_challenge_in_report():
    report = analyze_html("<html><title>Just a moment...</title></html>")
    assert report.looks_like_challenge
    assert "CHALLENGE WALL DETECTED" in report.render()


def test_analyze_large_cf_page_is_not_a_challenge():
    html = (
        "<html><body>"
        + "x" * 200_000
        + ("<script src='/cdn-cgi/challenge-platform'></script></body></html>")
    )
    report = analyze_html(html)
    assert not report.looks_like_challenge
    assert report.cloudflare_infra  # still noted as behind CF


def test_weak_markers_suppressed_when_real_content_present():
    # CF infra markers on a SMALL page that nonetheless carries real content
    # (has_real_content=True) -> the browser cleared the challenge; not a wall.
    small_with_markers = "<script src='/cdn-cgi/challenge-platform'></script>"
    assert detect_challenge(small_with_markers)  # no content signal -> wall
    assert detect_challenge(small_with_markers, has_real_content=True) == []


def test_analyze_small_cf_page_with_chapter_links_is_not_a_challenge():
    # Regression for the C9 false positive: a fully-rendered MangaFire-shaped
    # page -- under 100k chars, carries a `turnstile` CF script tag, but ALSO
    # has real chapter links -- must NOT be reported as a challenge wall.
    html = (
        "<html><body>"
        "<script src='/cdn-cgi/challenge-platform/turnstile'></script>"
        "<ul>"
        '<li><a href="/read/ad-astra.lww3/en/chapter-81" data-number="81">Chap 81</a></li>'
        '<li><a href="/read/ad-astra.lww3/en/chapter-80" data-number="80">Chap 80</a></li>'
        "</ul>"
        "</body></html>"
    )
    report = analyze_html(html)
    assert not report.looks_like_challenge  # real content -> cleared, not a wall
    assert report.chapter_links  # the content the browser actually rendered
    assert report.cloudflare_infra  # still noted as behind CF (informational)


def test_analyze_small_cf_page_without_content_still_flags_challenge():
    # The other side: a small CF page with NO real content is still a wall.
    html = (
        "<html><body><script src='/cdn-cgi/challenge-platform'></script></body></html>"
    )
    report = analyze_html(html)
    assert report.looks_like_challenge


# ========================= compare_fetches / strategy ====================


def test_strategy_requests_when_equivalent():
    page = "<html><body>" + "x" * 5000 + "</body></html>"
    cmp = compare_fetches(page, 200, page)
    assert cmp.strategy() == "requests"
    assert "RequestsFetcher" in cmp.recommend()


def test_strategy_headless_when_requests_challenged_but_browser_clears():
    challenge = "<title>Just a moment...</title>"
    real = "<html><body>" + "x" * 5000 + "</body></html>"
    cmp = compare_fetches(challenge, 403, real)
    assert cmp.strategy() == "nodriver-headless"


def test_strategy_headless_when_dynamic():
    sparse = "<html><body></body></html>"
    rich = "<html><body>" + "x" * 5000 + "</body></html>"
    cmp = compare_fetches(sparse, 200, rich)
    assert cmp.dynamic
    assert cmp.strategy() == "nodriver-headless"


def test_strategy_manual_when_challenge_persists_in_browser():
    challenge = "<title>Just a moment...</title>"
    cmp = compare_fetches(challenge, 403, challenge)
    assert cmp.strategy() == "nodriver-manual"
    assert "BARGE-IN" in cmp.recommend()


def test_strategy_headless_when_requests_errors_but_browser_works():
    real = "<html><body>" + "x" * 5000 + "</body></html>"
    cmp = compare_fetches(None, None, real, requests_error="dns fail")
    assert cmp.strategy() == "nodriver-headless"


def test_strategy_unknown_when_both_fail():
    cmp = compare_fetches(None, None, "", requests_error="dns fail")
    assert cmp.strategy() == "unknown"


# ============================== find_text ================================


def test_find_text_locates_attribute_value():
    html = '<html><body><a class="chico" href="/x/chapter_55">Ch 55</a></body></html>'
    matches = find_text(html, "chapter_55")
    hrefs = [m for m in matches if m.where == "href"]
    assert hrefs
    assert "a.chico" in hrefs[0].selector
    assert "chapter_55" in hrefs[0].snippet


def test_find_text_locates_visible_text_and_container():
    html = (
        "<table class='listing'><tr><td>"
        "<a class='chico'>Chapter 165</a>"
        "</td></tr></table>"
    )
    matches = find_text(html, "165")
    text_hits = [m for m in matches if m.where == "text"]
    assert text_hits
    # the innermost element holding the text, with its ancestor container path
    assert "a.chico" in text_hits[0].selector
    assert "table.listing" in text_hits[0].path


def test_find_text_not_found_is_empty():
    assert find_text("<html><body>nothing here</body></html>", "999") == []
    assert "not found" in render_matches("999", []).lower()


def test_find_text_against_real_fixture():
    # locate a known chapter number in the real mangakaka page and confirm it
    # points at a chapter anchor inside a container -- the manual-inspection win
    html = Path("tests/test_files/mangakaka/dragonball_super_page.html").read_text(
        encoding="utf-8"
    )
    matches = find_text(html, "chapter_55")
    assert matches
    # at least one match is a chapter href on an <a>
    assert any(m.where == "href" and "a" in m.selector for m in matches)


def test_element_selector_formats_id_and_classes():
    from bs4 import BeautifulSoup

    tag = BeautifulSoup("<div id='Read' class='a b c d'></div>", "lxml").find("div")
    sel = _element_selector(tag)
    assert sel.startswith("div#Read")
    # caps classes at 3
    assert sel.count(".") == 3


# ===================== API response-body capture =========================


def test_is_api_like_url_matches_ajax_api_json():
    assert is_api_like_url("https://site/api/search?q=naruto")
    assert is_api_like_url("https://site/ajax/manga/read/123")
    assert is_api_like_url("https://site/data/list.json")
    # query string is ignored when deciding
    assert is_api_like_url("https://site/api/v2/manga?id=9&page=2")


def test_is_api_like_url_rejects_plain_pages_and_assets():
    assert not is_api_like_url("https://site/manga/dragon-ball")
    assert not is_api_like_url("https://site/home")
    assert not is_api_like_url("https://site/static/app.js")


def test_is_json_mime():
    assert is_json_mime("application/json")
    assert is_json_mime("application/json; charset=utf-8")
    assert is_json_mime("text/json")
    assert is_json_mime("application/vnd.api+json")
    assert not is_json_mime("text/html")
    assert not is_json_mime("")
    assert not is_json_mime(None)


def test_api_dump_filename_is_safe_and_indexed():
    assert (
        api_dump_filename(3, "https://mangak.io/api/search?q=naruto")
        == "api_03_search.json"
    )
    # trailing slash + nested path -> last meaningful segment
    assert api_dump_filename(1, "https://x/api/manga/list/") == "api_01_list.json"
    # already-.json path is preserved (not doubled)
    assert api_dump_filename(12, "https://x/data/v2.json") == "api_12_v2.json"
    # empty path falls back to a default name
    assert api_dump_filename(2, "https://x") == "api_02_response.json"


def test_dump_api_bodies_writes_files_and_index(tmp_path):
    from scraper.probe import _dump_api_bodies

    bodies = [
        ("https://x/api/search?q=naruto", '{"results":[{"title":"Naruto"}]}'),
        ("https://x/api/manifest", "not-json-raw"),
    ]
    _dump_api_bodies(tmp_path, bodies)

    index = (tmp_path / "api_index.txt").read_text(encoding="utf-8")
    assert "2 API/JSON response" in index
    assert "api_01_search.json" in index
    assert "api_02_manifest.json" in index

    # JSON body is pretty-printed (indented), raw body kept verbatim
    search = (tmp_path / "api_01_search.json").read_text(encoding="utf-8")
    assert '"title": "Naruto"' in search  # space after colon == indented json
    raw = (tmp_path / "api_02_manifest.json").read_text(encoding="utf-8")
    assert raw == "not-json-raw"


def test_dump_api_bodies_handles_empty(tmp_path):
    from scraper.probe import _dump_api_bodies

    _dump_api_bodies(tmp_path, [])
    index = (tmp_path / "api_index.txt").read_text(encoding="utf-8")
    assert "no API/JSON response bodies captured" in index


def test_dump_api_bodies_records_misses(tmp_path):
    from scraper.probe import _dump_api_bodies

    # bodies captured + an endpoint we saw but couldn't read
    bodies = [("https://x/api/search?q=n", '{"a":1}')]
    misses = ["https://x/api/evicted", "https://x/api/evicted"]  # dup collapses
    _dump_api_bodies(tmp_path, bodies, misses)
    index = (tmp_path / "api_index.txt").read_text(encoding="utf-8")
    assert "could not read a body" in index
    assert index.count("https://x/api/evicted") == 1


def test_dump_api_bodies_misses_only(tmp_path):
    from scraper.probe import _dump_api_bodies

    _dump_api_bodies(tmp_path, [], ["https://x/api/evicted"])
    index = (tmp_path / "api_index.txt").read_text(encoding="utf-8")
    assert "no API/JSON response bodies captured" in index
    assert "https://x/api/evicted" in index


# ===================== API backend reachability ==========================


def test_looks_like_json_accepts_objects_and_arrays():
    assert looks_like_json('{"a": 1}')
    assert looks_like_json("  [1, 2, 3]  ")  # leading whitespace tolerated
    assert looks_like_json('{"data": {"items": []}}')


def test_looks_like_json_rejects_html_and_junk():
    assert not looks_like_json("<html><title>Just a moment...</title></html>")
    assert not looks_like_json("")
    assert not looks_like_json(None)
    assert not looks_like_json("{not valid json")


def test_summarize_backend_probe_reports_json_ok():
    line = summarize_backend_probe("requests", 200, None, '{"data": {"items": []}}')
    assert "status=200" in line
    assert "json=True" in line
    assert "JSON OK" in line


def test_summarize_backend_probe_flags_blocked_html():
    line = summarize_backend_probe("requests", 403, None, "<html>nope</html>")
    assert "status=403" in line
    assert "json=False" in line
    assert "blocked" in line


def test_summarize_backend_probe_reports_error():
    line = summarize_backend_probe("curl_cffi", None, "dns fail", None)
    assert "ERROR" in line
    assert "dns fail" in line


# ==================== json_paths_for_value (locator) =====================
# Map-by-example core: give a value seen on the page, get the JSON path(s) it
# lives at. Exercised against the real mangabuddy search/chapter-list fixtures.

_MANGABUDDY = Path("tests/test_files/mangabuddy")


def _load_json(name: str) -> object:
    return json.loads((_MANGABUDDY / name).read_text(encoding="utf-8"))


def _exact_string_paths(obj: object, value: str, path: str = "") -> list[str]:
    """Independently collect every path whose leaf is a string == ``value``.

    A second, deliberately naive implementation used to check the locator's
    completeness for exact matches (Property 2) without trusting the locator's
    own traversal.
    """
    found: list[str] = []
    if isinstance(obj, dict):
        for key, child in obj.items():
            child_path = f"{path}.{key}" if path else key
            found.extend(_exact_string_paths(child, value, child_path))
    elif isinstance(obj, list):
        for i, child in enumerate(obj):
            found.extend(_exact_string_paths(child, value, f"{path}[{i}]"))
    elif isinstance(obj, str) and obj == value:
        found.append(path)
    return found


def test_locator_finds_exact_slug_and_name():
    # Req 2.1: the slug "naruto" and the name "Naruto" each resolve to their
    # own exact leaf in the first search result.
    data = _load_json("search_naruto.json")

    slug_matches = json_paths_for_value(data, "naruto")
    exact_slug = [m for m in slug_matches if m.kind == "exact"]
    assert [m.path for m in exact_slug] == ["data.items[0].slug"]
    assert exact_slug[0].leaf == "naruto"

    name_matches = json_paths_for_value(data, "Naruto")
    exact_name = [m for m in name_matches if m.kind == "exact"]
    assert [m.path for m in exact_name] == ["data.items[0].name"]
    assert exact_name[0].leaf == "Naruto"


def test_locator_is_complete_for_exact_matches():
    # Property 2 / Req 2.1: no exact-equal leaf anywhere is dropped. Compare the
    # locator's exact hits against an independent scan of the structure.
    data = _load_json("search_naruto.json")
    for value in ("naruto", "Naruto", "completed", "ongoing"):
        located = {
            m.path for m in json_paths_for_value(data, value) if m.kind == "exact"
        }
        assert located == set(_exact_string_paths(data, value))


def test_locator_substring_match_inside_chapter_name():
    # Req 2.2: "700.5" is not a leaf of its own; it lives inside the chapter
    # name "Chapter 700.5 : Uzumaki Naruto" and must be flagged substring.
    chapters = _load_json("chapters_naruto.json")
    matches = json_paths_for_value(chapters, "700.5")
    by_path = {m.path: m for m in matches}

    assert "data.chapters[0].name" in by_path
    hit = by_path["data.chapters[0].name"]
    assert hit.kind == "substring"
    assert hit.leaf == "Chapter 700.5 : Uzumaki Naruto"
    # the dotted/slugged forms use "700-5", so the only place "700.5" appears is
    # the human-readable name -- every match for it is a substring match.
    assert all(m.kind == "substring" for m in matches)


def test_locator_numeric_string_equivalence():
    # Req 2.3: the string "748" matches the numeric chapter_number / chapters
    # count leaves (748), cross-type, marked numeric.
    data = _load_json("search_naruto.json")
    matches = json_paths_for_value(data, "748")

    numeric_paths = {m.path for m in matches if m.kind == "numeric"}
    assert numeric_paths == {
        "data.items[0].stats.chapters_count",
        "data.items[0].latest_chapters[0].chapter_number",
    }
    for m in matches:
        assert m.kind == "numeric"
        assert m.leaf == 748


def test_locator_path_prefix_distinct_from_exact_slug():
    # Req 2.4: "naruto" matches the url leaf "/naruto" as a path-prefix match,
    # and this is reported separately from the exact slug match "naruto".
    data = _load_json("search_naruto.json")
    matches = json_paths_for_value(data, "naruto")
    by_path = {m.path: m for m in matches}

    assert by_path["data.items[0].url"].kind == "path-prefix"
    assert by_path["data.items[0].url"].leaf == "/naruto"
    assert by_path["data.items[0].slug"].kind == "exact"
    assert by_path["data.items[0].slug"].leaf == "naruto"
    # distinct paths, distinct kinds for the same target value
    assert by_path["data.items[0].url"].kind != by_path["data.items[0].slug"].kind


def test_locator_lists_second_item_substring_matches():
    # Req 2.1/2.2: account for the second item, whose slug
    # "naruto-the-seventh-hokage-reborn" contains "naruto" -- a substring (not
    # exact) match that must still be reported, not collapsed into the first.
    data = _load_json("search_naruto.json")
    matches = json_paths_for_value(data, "naruto")
    by_path = {m.path: m for m in matches}

    assert by_path["data.items[1].slug"].kind == "substring"
    assert by_path["data.items[1].url"].kind == "substring"


def test_locator_reports_unresolved_as_empty_list():
    # Req 2.5: a value present nowhere yields an empty result (the caller then
    # reports it as unresolved rather than dropping it silently).
    for name in ("search_naruto.json", "chapters_naruto.json"):
        data = _load_json(name)
        assert json_paths_for_value(data, "zzz-nonexistent") == []
    # an empty target is noise, not a location -> also empty
    assert json_paths_for_value(_load_json("search_naruto.json"), "") == []


def test_locator_soundness_paths_resolve_back():
    # Property 1 / Req 2.1, 2.6: every returned path resolves back to exactly
    # the matched leaf via get_by_path -- the path is real and points at it.
    cases = [
        ("search_naruto.json", "naruto"),
        ("search_naruto.json", "Naruto"),
        ("search_naruto.json", "748"),
        ("chapters_naruto.json", "700.5"),
        ("chapters_naruto.json", "748"),
    ]
    for name, value in cases:
        data = _load_json(name)
        matches = json_paths_for_value(data, value)
        assert matches  # each case has at least one hit
        for m in matches:
            assert get_by_path(data, m.path) == m.leaf
            assert isinstance(m, PathMatch)


def test_locator_handles_arbitrary_nested_structure_without_raising():
    # Req 2.6: traverses nested objects/arrays to a bounded depth and does not
    # raise on arbitrary (cyclic-free) JSON shapes -- empty containers, None,
    # booleans, mixed lists, deep nesting.
    weird = {
        "a": [1, 2, {"b": [{"c": "naruto"}, None, [True, "x"]]}],
        "deep": {"e": {"f": {"g": {"h": "naruto"}}}},
        "empty_obj": {},
        "empty_list": [],
        "flag": False,
        "num": 748,
        "mixed": [{"k": "v"}, "naruto", 3.14, None],
    }
    matches = json_paths_for_value(weird, "naruto")
    # found in the three string-leaf spots, and each path round-trips
    assert {m.path for m in matches} == {
        "a[2].b[0].c",
        "deep.e.f.g.h",
        "mixed[1]",
    }
    for m in matches:
        assert get_by_path(weird, m.path) == m.leaf
    # a numeric target still resolves on this structure without raising
    for m in json_paths_for_value(weird, "748"):
        assert get_by_path(weird, m.path) == m.leaf


# ==================== sibling_mismatch_check (gotcha) ====================
# Once a value is located, the checker inspects the fields *next to* it and
# warns when a sibling looks like it should carry the same number but disagrees
# -- the mangak.io gotcha where a chapter's displayed number ("700.5") sits
# beside a chapter_number that is really a sequence counter (748). Hints only:
# matching siblings never warn, so the absence of warnings is meaningful
# (Req 3.4, Property 4). Exercised against the real chapters fixture.


def _match_at(matches: list[PathMatch], path: str) -> PathMatch:
    """The single PathMatch at ``path`` (fails loudly if absent)."""
    by_path = {m.path: m for m in matches}
    assert path in by_path, f"expected a match at {path}, got {sorted(by_path)}"
    return by_path[path]


def test_gotcha_flags_chapter_number_sequence_counter_mangakio():
    # Req 3.5: the mangak.io case. "700.5" is the displayed chapter number,
    # located inside data.chapters[0].name; its sibling chapter_number=748 is a
    # sequence counter that disagrees and must be flagged as an advisory hint.
    chapters = _load_json("chapters_naruto.json")
    match = _match_at(json_paths_for_value(chapters, "700.5"), "data.chapters[0].name")
    assert match.leaf == "Chapter 700.5 : Uzumaki Naruto"

    warnings = sibling_mismatch_check(chapters, match, "700.5")

    # the chapter_number sibling is surfaced. cv is also a disagreeing numeric
    # sibling (Req 3.2 "any numeric sibling"), so assert presence -- not count.
    by_sibling = {w.sibling_path: w for w in warnings}
    assert "data.chapters[0].chapter_number" in by_sibling
    w = by_sibling["data.chapters[0].chapter_number"]
    assert isinstance(w, SiblingWarning)

    # both the target number (700.5) and the sequence counter (748) are surfaced
    assert w.sibling_value == 748
    assert "748" in w.message
    assert "700.5" in w.message

    # advisory hint wording: names the likely interpretation (a sequence
    # counter, derive from the matched field) and never asserts it is "wrong".
    assert "likely" in w.message
    assert "sequence counter" in w.message
    assert "derive" in w.message
    assert "wrong" not in w.message.lower()


def test_gotcha_no_warning_when_sibling_agrees():
    # Req 3.4 / Property 4: a sibling that agrees with the target number emits
    # NO warning, so the absence of warnings is meaningful. The agreeing
    # chapter_number=748 sits beside the matched name "Chapter 748".
    obj = {"name": "Chapter 748", "chapter_number": 748}
    match = _match_at(json_paths_for_value(obj, "Chapter 748"), "name")
    assert match.kind == "exact"
    assert sibling_mismatch_check(obj, match, "Chapter 748") == []


def test_gotcha_no_warning_when_target_has_no_number():
    # Req 3.4: with no number in the target there is nothing for even a numeric
    # sibling (year=1999) to disagree with -> stay silent (no noise).
    obj = {"title": "Naruto", "year": 1999}
    match = _match_at(json_paths_for_value(obj, "Naruto"), "title")
    assert sibling_mismatch_check(obj, match, "Naruto") == []


def test_gotcha_no_warning_for_non_numeric_siblings():
    # Req 3.4: a numeric target but every sibling is a non-numeric string
    # (slug/id) -> nothing to compare numerically -> no warning.
    obj = {"name": "Chapter 700.5", "slug": "chapter-700-5", "id": "WYXlbzbY"}
    match = _match_at(json_paths_for_value(obj, "700.5"), "name")
    assert sibling_mismatch_check(obj, match, "700.5") == []


def test_gotcha_no_named_siblings_for_list_element_or_root():
    # Req 3.1: a matched leaf with no dict parent (a list element, or the root
    # value itself) has no named siblings -> [].
    list_match = _match_at(json_paths_for_value(["748", "x"], "748"), "[0]")
    assert sibling_mismatch_check(["748", "x"], list_match, "748") == []

    root_matches = json_paths_for_value("748", "748")
    assert root_matches and root_matches[0].path == ""
    assert sibling_mismatch_check("748", root_matches[0], "748") == []


# ===================== build_field_map / render_field_map ================
# Phase 2 surface: tie the locator + gotcha checker across every captured JSON
# body, one entry per developer-supplied example. Pure -- parsed JSON in, report
# text out; the CLI wrapper (task 10) does the file IO / __NEXT_DATA__ pull.


def test_field_map_one_entry_per_example_in_order():
    # Req 4.3 / Property 3: every example produces exactly one entry, in the
    # input order -- nothing is dropped or reordered.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    examples = {"title": "Naruto", "slug": "naruto", "chapters": "748"}
    entries = build_field_map(captures, examples)

    assert [e.name for e in entries] == ["title", "slug", "chapters"]
    assert [e.value for e in entries] == ["Naruto", "naruto", "748"]
    assert all(isinstance(e, FieldMapEntry) for e in entries)


def test_field_map_lists_all_matches_with_source_file():
    # Req 4.2 / 4.4 / Property 6: all matches are listed (the tool does not pick
    # "the" field) and each carries the capture label it came from.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    entries = build_field_map(captures, {"slug": "naruto"})

    (entry,) = entries
    # exact slug + path-prefix url + the second item's substrings all show up
    paths = {(src, m.path) for src, m in entry.matches}
    assert ("search_naruto.json", "data.items[0].slug") in paths
    assert ("search_naruto.json", "data.items[0].url") in paths
    assert len(entry.matches) >= 2
    # every match keeps its source-file label
    assert all(src == "search_naruto.json" for src, _ in entry.matches)


def test_field_map_searches_every_capture():
    # Req 4.2: the locator runs over EVERY capture; a value present in two
    # captures is reported once per capture (uniform across api_*.json and
    # embedded payloads -- here two arbitrary parsed bodies).
    captures = {
        "api_a.json": {"title": "Naruto"},
        "api_b.json": {"meta": {"title": "Naruto"}},
    }
    (entry,) = build_field_map(captures, {"title": "Naruto"})
    by_src = {src: m for src, m in entry.matches}
    assert by_src["api_a.json"].path == "title"
    assert by_src["api_b.json"].path == "meta.title"


def test_field_map_unresolved_entry_has_empty_matches():
    # Req 4.3 / Property 3: a value found nowhere still yields an entry, with an
    # empty matches list -- that is how "unresolved" is represented.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    (entry,) = build_field_map(captures, {"mystery": "zzz-nonexistent"})
    assert entry.matches == []
    assert entry.warnings == []


def test_field_map_collects_sibling_warnings():
    # Req 4.2 + Req 3: the gotcha hints for each match are gathered onto the
    # entry. The mangak.io case: name "Chapter 700.5" beside chapter_number=748.
    captures = {
        "cap": {
            "data": {
                "chapters": [
                    {
                        "name": "Chapter 700.5 : Uzumaki Naruto",
                        "chapter_number": 748,
                    }
                ]
            }
        }
    }
    (entry,) = build_field_map(captures, {"chapter": "700.5"})
    assert entry.matches  # located inside the name
    assert any("sequence counter" in w.message for w in entry.warnings)


def test_field_map_dedupes_recurring_warnings():
    # A sibling hint that recurs identically across captures is reported once
    # (de-duped by sibling_path + message) to keep the report quiet.
    body = {"name": "Chapter 700.5", "chapter_number": 748}
    captures = {"api_a.json": body, "api_b.json": dict(body)}
    (entry,) = build_field_map(captures, {"chapter": "700.5"})
    # two captures -> two matches, but the identical hint is listed once
    assert len(entry.matches) == 2
    assert len(entry.warnings) == 1


def test_render_field_map_is_advisory_and_lists_all_matches():
    # Property 6 / Req 4.4: the report is labeled advisory and lists every
    # matching path with its source + kind, never asserting a single answer.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    entries = build_field_map(captures, {"slug": "naruto"})
    text = render_field_map(entries)

    assert "advisory" in text.lower()
    assert "slug" in text
    assert "data.items[0].slug" in text
    assert "data.items[0].url" in text
    assert "search_naruto.json" in text
    # match kinds are surfaced so the dev can interpret
    assert "[exact]" in text
    assert "[path-prefix]" in text


def test_render_field_map_has_unresolved_section():
    # Req 4.3 / Property 3: unresolved examples are surfaced in a dedicated
    # section, not dropped silently.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    entries = build_field_map(
        captures, {"title": "Naruto", "mystery": "zzz-nonexistent"}
    )
    text = render_field_map(entries)

    assert "Unresolved" in text
    assert "mystery" in text
    assert "zzz-nonexistent" in text


def test_render_field_map_includes_warning_hints():
    # The gotcha hints appear in the rendered report (phrased as hints).
    captures = {"cap": {"name": "Chapter 700.5", "chapter_number": 748}}
    entries = build_field_map(captures, {"chapter": "700.5"})
    text = render_field_map(entries)
    assert "hint" in text.lower()
    assert "sequence counter" in text


def test_render_field_map_empty_input():
    # No examples -> a clear, non-crashing report.
    assert "no values supplied" in render_field_map([]).lower()


def test_build_field_map_is_pure_does_not_mutate_inputs():
    # Property 7: pure function of its inputs -- captures/examples unchanged.
    captures = {"search_naruto.json": _load_json("search_naruto.json")}
    snapshot = json.dumps(captures, sort_keys=True)
    examples = {"slug": "naruto"}
    build_field_map(captures, examples)
    assert json.dumps(captures, sort_keys=True) == snapshot
    assert examples == {"slug": "naruto"}


# =============== --map-by-example CLI + field_map.txt (task 10) ==========
# The map-by-example surface: parse name=value CLI tokens, load the EXISTING
# captures from the output dir (no re-capture / no browser, Req 7.1), and write
# field_map.txt. The parsing + loading + report rendering are exercised against
# the real mangabuddy fixtures (Req 4.5).


def _seed_captures(out_dir: Path) -> None:
    """Copy the real mangabuddy fixtures into ``out_dir`` as api_*.json dumps,
    plus a malformed body that the loader must skip gracefully."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "api_01_search.json").write_text(
        (_MANGABUDDY / "search_naruto.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (out_dir / "api_02_chapters.json").write_text(
        (_MANGABUDDY / "chapters_naruto.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # malformed JSON capture -> must be skipped, not raised on
    (out_dir / "api_03_bad.json").write_text("{not valid json", encoding="utf-8")


# --------------------------- parse_examples ------------------------------


def test_parse_examples_splits_name_value_pairs():
    # Req 4.1: name=value tokens become an ordered {name: value} map.
    assert parse_examples(["title=Naruto", "slug=naruto"]) == {
        "title": "Naruto",
        "slug": "naruto",
    }


def test_parse_examples_value_may_contain_equals():
    # split on the FIRST '=' so a value can itself contain '='
    assert parse_examples(["url=/x?a=b&c=d"]) == {"url": "/x?a=b&c=d"}


def test_parse_examples_preserves_order():
    examples = parse_examples(["a=1", "b=2", "c=3"])
    assert list(examples) == ["a", "b", "c"]


def test_parse_examples_rejects_token_without_equals():
    # a token with no '=' cannot name a value -> clear error
    with pytest.raises(ValueError):
        parse_examples(["title=Naruto", "noequals"])


# ----------------------------- load_captures -----------------------------


def test_load_captures_parses_api_json_keyed_by_filename(tmp_path):
    # Req 4.2: every api_*.json is parsed and keyed by its filename.
    _seed_captures(tmp_path)
    captures = load_captures(tmp_path)

    assert set(captures) == {"api_01_search.json", "api_02_chapters.json"}
    # the malformed api_03_bad.json is skipped, not raised on
    assert "api_03_bad.json" not in captures
    # real parsed structure is returned (paths resolve through it)
    assert get_by_path(captures["api_01_search.json"], "data.items[0].slug") == "naruto"


def test_load_captures_extracts_embedded_next_data(tmp_path):
    # Req 4.2: __NEXT_DATA__ embedded in page.html is searched too.
    (tmp_path / "page.html").write_text(
        (_MANGABUDDY / "chapter_page.html").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    captures = load_captures(tmp_path)
    assert "page.html#__NEXT_DATA__" in captures
    # the embedded payload is the parsed Next.js data (has the props tree)
    assert isinstance(captures["page.html#__NEXT_DATA__"], dict)
    assert "props" in captures["page.html#__NEXT_DATA__"]


def test_load_captures_missing_dir_is_empty(tmp_path):
    # design: missing files/dir treated as empty, never raised on
    assert load_captures(tmp_path / "does-not-exist") == {}


def test_load_captures_no_artifacts_is_empty(tmp_path):
    assert load_captures(tmp_path) == {}


# ------------------- write_field_map (IO wrapper) ------------------------


def test_write_field_map_writes_report_over_fixtures(tmp_path):
    # Req 4.3 / 4.5: the wrapper writes field_map.txt locating each value across
    # the captures, with paths, source files, gotcha hint, and an unresolved
    # section -- all against the real fixtures.
    _seed_captures(tmp_path)
    examples = {
        "name": "Naruto",
        "slug": "naruto",
        "chapters": "748",
        "missing": "zzz",
    }
    path = write_field_map(tmp_path, examples)

    assert path == tmp_path / "field_map.txt"
    text = path.read_text(encoding="utf-8")

    # advisory header (Property 6)
    assert "advisory" in text.lower()
    # resolved paths + their source file
    assert "data.items[0].slug" in text
    assert "api_01_search.json" in text
    # the chapter gotcha hint surfaces (chapter_number=748 is a sequence counter
    # beside "Chapter 700.5"); 748 is located as a numeric match here.
    assert "748" in text
    assert "sequence counter" in text
    # unresolved section lists the value found nowhere
    assert "Unresolved" in text
    assert "missing" in text
    assert "zzz" in text


# ----------------------- end-to-end via main() ---------------------------


def test_main_map_by_example_writes_field_map_without_browser(tmp_path, monkeypatch):
    # Req 4.1/4.5 + Req 7.1: the --map-by-example branch builds the field map
    # from the existing captures and returns WITHOUT importing/driving nodriver.
    # We seed the out_dir, then make any browser path explode so the test fails
    # loudly if main() ever tries to capture.
    out_base = tmp_path
    out_dir = out_base / "mybuddy"
    _seed_captures(out_dir)

    def _boom(*_a, **_k):
        raise AssertionError("map-by-example must not drive a browser (Req 7.1)")

    # if the branch fell through to a capture, this would fire
    monkeypatch.setattr("scraper.probe._probe", _boom)

    rc = main(
        [
            "https://example.test/manga/naruto",
            "--out",
            str(out_base),
            "--site",
            "mybuddy",
            "--map-by-example",
            "name=Naruto",
            "slug=naruto",
            "chapters=748",
            "missing=zzz",
        ]
    )

    assert rc == 0
    field_map = out_dir / "field_map.txt"
    assert field_map.is_file()
    text = field_map.read_text(encoding="utf-8")
    # multiple pairs were accepted and located (Req 4.1)
    assert "data.items[0].slug" in text
    assert "api_01_search.json" in text
    assert "sequence counter" in text  # the 748 chapter gotcha hint
    # unresolved value surfaced, not dropped
    assert "Unresolved" in text
    assert "zzz" in text


def test_main_map_by_example_accepts_multiple_pairs(tmp_path):
    # Req 4.1: the option takes one OR many name=value pairs.
    out_dir = tmp_path / "site"
    _seed_captures(out_dir)
    rc = main(
        [
            "https://example.test/x",
            "--out",
            str(tmp_path),
            "--site",
            "site",
            "--map-by-example",
            "a=Naruto",
            "b=naruto",
            "c=completed",
        ]
    )
    assert rc == 0
    text = (out_dir / "field_map.txt").read_text(encoding="utf-8")
    for label in ("a", "b", "c"):
        assert label in text


def test_main_map_by_example_rejects_bad_token(tmp_path):
    # a token with no '=' is a clean CLI error (argparse SystemExit), not a crash
    out_dir = tmp_path / "site"
    _seed_captures(out_dir)
    with pytest.raises(SystemExit):
        main(
            [
                "https://example.test/x",
                "--out",
                str(tmp_path),
                "--site",
                "site",
                "--map-by-example",
                "noequals",
            ]
        )


# ============== synthesize_recommendation (Phase 1, task 3) ==============
# Phase 1 distils the capture artifacts (ajax log, api_backends verdicts,
# captured bodies, page HTML) into ONE advisory recommendation. These tests use
# crafted inputs mirroring real captures: an open-API mangak.io-like site, a
# challenged-everywhere site, a plain-HTML site with no API, plus API-served
# images and empty/defensive inputs. Pure -- no browser/network (Req 1.6,
# Property 7); advisory framing is asserted (Property 6, Req 1.5).


def _stage(rec: Recommendation, name: str) -> StagePlan:
    """The single StagePlan named ``name`` (fails loudly if absent)."""
    by_name = {s.name: s for s in rec.stages}
    assert name in by_name, f"expected a {name} stage, got {sorted(by_name)}"
    return by_name[name]


def _next_data_html(images: list[str]) -> str:
    """A minimal chapter page whose embedded ``__NEXT_DATA__`` carries an images
    array under ``props.pageProps.initialChapter.images`` (the mangabuddy shape
    that ``_images_from_chapter_payload`` reads)."""
    payload = {"props": {"pageProps": {"initialChapter": {"images": images}}}}
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload)
        + "</script></body></html>"
    )


def test_synthesize_open_api_site_curl_cffi_with_embedded_images():
    # Req 1.2 + 1.4 (mangak.io-like): the search + chapters endpoints answer
    # curl_cffi (JSON without a browser), and there is NO image-list endpoint --
    # the images live embedded in the page __NEXT_DATA__.
    search_url = "https://api.mangak.io/titles/search?q=naruto"
    chapters_url = "https://api.mangak.io/titles/123/chapters?cv=ABC"
    search_body = json.dumps(
        {"data": {"items": [{"id": "abc", "slug": "naruto", "name": "Naruto"}]}}
    )
    chapters_body = json.dumps(
        {"data": {"chapters": [{"slug": "chapter-1", "name": "Chapter 1"}]}}
    )
    page_html = _next_data_html(
        ["https://cdn.example/x/1.webp", "https://cdn.example/x/2.webp"]
    )

    rec = synthesize_recommendation(
        ajax_urls=[search_url, chapters_url],
        api_backends={search_url: "curl_cffi", chapters_url: "curl_cffi"},
        api_bodies=[(search_url, search_body), (chapters_url, chapters_body)],
        page_html=page_html,
    )

    # Req 1.2: an open JSON API reachable without a browser -> prefer curl_cffi
    assert rec.api_open is True
    assert rec.default_fetcher == "curl_cffi"

    # search + chapters point at the real endpoints with the curl_cffi fetcher
    search = _stage(rec, "search")
    assert search.mechanism == search_url
    assert search.fetcher == "curl_cffi"
    chapters = _stage(rec, "chapters")
    assert chapters.mechanism == chapters_url
    assert chapters.fetcher == "curl_cffi"

    # Req 1.4: images are EMBEDDED in the page payload, NOT API-served
    images = _stage(rec, "images")
    assert "__NEXT_DATA__" in images.mechanism
    assert "__NEXT_DATA__" in images.note
    assert "EMBEDDED" in images.note
    assert "embedded" in images.note.lower()
    assert images.fetcher == "curl_cffi"

    # Property 6 / Req 1.1, 1.5: advisory framing + hosts + every stage surface
    text = rec.render()
    assert "advisory" in text.lower()
    assert "SUGGESTIONS" in text
    assert "api.mangak.io" in text
    assert "open JSON API found: yes" in text
    assert "suggested default fetcher: curl_cffi" in text
    for name in ("search", "chapters", "images"):
        assert f"[{name}]" in text
    assert search_url in text
    assert chapters_url in text


def test_synthesize_challenged_everywhere_site_needs_browser():
    # Req 1.3: every endpoint is blocked to non-browser clients, but the browser
    # captured a body -> the recommendation falls back to BrowserFetcher, and
    # the stage whose blocked endpoint has a captured body is "browser".
    search_url = "https://api.walled.example/titles/search?q=x"
    chapters_url = "https://api.walled.example/titles/9/chapters?cv=Z"
    chapters_body = json.dumps({"data": {"chapters": [{"slug": "c1", "name": "Ch 1"}]}})

    rec = synthesize_recommendation(
        ajax_urls=[search_url, chapters_url],
        api_backends={search_url: "blocked", chapters_url: "blocked"},
        api_bodies=[(chapters_url, chapters_body)],
        page_html=None,
    )

    assert rec.api_open is False
    assert rec.default_fetcher == "browser"

    # the chapters endpoint is blocked but a browser captured its body -> browser
    chapters = _stage(rec, "chapters")
    assert chapters.mechanism == chapters_url
    assert chapters.fetcher == "browser"


def test_synthesize_no_api_html_site_degrades_gracefully():
    # A plain-HTML site: no API-like endpoints, no backends, no bodies, and a
    # page with no embedded images. Every stage degrades to a clear "(no ...)"
    # mechanism without raising.
    rec = synthesize_recommendation(
        ajax_urls=[
            "https://plainmanga.example/manga/dragon-ball",
            "https://plainmanga.example/home",
        ],
        api_backends={},
        api_bodies=[],
        page_html="<html><body><h1>Dragon Ball</h1></body></html>",
    )

    assert rec.api_open is False
    assert rec.default_fetcher == "unknown"

    assert _stage(rec, "search").mechanism == "(no search endpoint seen)"
    assert _stage(rec, "chapters").mechanism == "(no chapters endpoint seen)"
    assert _stage(rec, "images").mechanism == "(no image source identified)"
    # render is still produced without raising
    assert "advisory" in rec.render().lower()


def test_synthesize_api_served_images_from_captured_body():
    # Req 1.4 (first branch): a captured body that IS an array of image URLs at
    # an /api/...images... endpoint -> the images stage is marked API-served.
    images_url = "https://cdn-api.example/api/book/1/images"
    images_body = json.dumps(["https://cdn/1.jpg", "https://cdn/2.jpg"])

    rec = synthesize_recommendation(
        ajax_urls=[images_url],
        api_backends={images_url: "curl_cffi"},
        api_bodies=[(images_url, images_body)],
        page_html=None,
    )

    images = _stage(rec, "images")
    assert images.mechanism == images_url
    assert images.fetcher == "curl_cffi"
    assert "API-served" in images.note


def test_synthesize_empty_inputs_are_defensive():
    # Req 1.6 / design "Error handling": fully empty inputs never raise; the
    # result is honest about having nothing (api_open False, unknown fetcher),
    # and still emits the three stage plans.
    rec = synthesize_recommendation([], {}, [], None)

    assert rec.api_open is False
    assert rec.default_fetcher == "unknown"
    assert [s.name for s in rec.stages] == ["search", "chapters", "images"]
    # render must not raise on the empty case either
    assert "(none seen)" in rec.render()


def test_recommendation_render_surfaces_all_fields():
    # Req 1.1, 1.5 / Property 6: render() surfaces the host(s), the open-API
    # verdict, the default fetcher, and each stage's name + mechanism, framed as
    # advisory suggestions -- and never raises on an empty Recommendation().
    rec = Recommendation(
        hosts=["api.mangak.io"],
        api_open=True,
        default_fetcher="curl_cffi",
        stages=[
            StagePlan("search", "api.mangak.io/titles/search", "curl_cffi"),
            StagePlan("chapters", "api.mangak.io/titles/9/chapters", "curl_cffi"),
            StagePlan(
                "images", "embedded in page __NEXT_DATA__", "curl_cffi", note="hint"
            ),
        ],
    )
    text = rec.render()

    # advisory header + framing
    assert "Recommended approach (advisory)" in text
    assert "SUGGESTIONS" in text
    # host list + open-API verdict + default fetcher
    assert "api.mangak.io" in text
    assert "open JSON API found: yes" in text
    assert "suggested default fetcher: curl_cffi" in text
    # each stage's name + mechanism
    assert "[search] api.mangak.io/titles/search" in text
    assert "[chapters] api.mangak.io/titles/9/chapters" in text
    assert "[images] embedded in page __NEXT_DATA__" in text

    # empty instance renders without raising and is honest about the gaps
    empty = Recommendation().render()
    assert "(none seen)" in empty
    assert "open JSON API found: no" in empty
    assert "no stage candidates identified" in empty
