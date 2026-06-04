"""
Tests for the probe's pure analysis helpers (no browser needed).
"""

from pathlib import Path

from scraper.probe import (
    ProbeReport,
    _element_selector,
    analyze_html,
    api_dump_filename,
    compare_fetches,
    detect_challenge,
    find_text,
    is_api_like_url,
    is_json_mime,
    looks_like_json,
    render_matches,
    site_name_from_url,
    summarize_backend_probe,
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
