"""
Tests for the bs4 typing helpers (scraper.parsers._html).
"""

from bs4 import BeautifulSoup

from scraper.parsers._html import attr, text


def _tag(html: str, name: str):
    return BeautifulSoup(html, "lxml").find(name)


def test_attr_returns_single_value_string():
    tag = _tag('<a href="/x/chapter-5">c</a>', "a")
    assert attr(tag, "href") == "/x/chapter-5"


def test_attr_missing_returns_default():
    tag = _tag("<a>c</a>", "a")
    assert attr(tag, "href") == ""
    assert attr(tag, "href", default="none") == "none"


def test_attr_none_tag_returns_default():
    assert attr(None, "href") == ""


def test_attr_multivalue_is_joined():
    # class is multi-valued in bs4 -> returns a list; attr joins to a str
    tag = _tag('<div class="a b c">x</div>', "div")
    assert attr(tag, "class") == "a b c"


def test_text_returns_text_or_default():
    tag = _tag("<h3>  Title </h3>", "h3")
    assert text(tag).strip() == "Title"
    assert text(None) == ""
    assert text(None, default="x") == "x"
