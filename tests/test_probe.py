"""
Tests for the probe's pure analysis helpers (no browser needed).
"""

from scraper.probe import ProbeReport, analyze_html, site_name_from_url

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
