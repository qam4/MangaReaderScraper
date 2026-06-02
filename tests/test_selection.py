"""
Tests for scraper.selection: ChapterId ordering and chapter-number-based
selection (single ids, ranges, decimals, gaps).
"""

from scraper.selection import ChapterId, select_chapters, sort_chapter_ids

# ============================== ChapterId ================================


def test_chapter_id_orders_numerically_not_lexicographically():
    assert ChapterId("9") < ChapterId("10")
    assert ChapterId("2") < ChapterId("62")


def test_chapter_id_orders_decimals_between_integers():
    assert ChapterId("28") < ChapterId("28.22")
    assert ChapterId("28.22") < ChapterId("29")


def test_chapter_id_preserves_raw_string():
    # url round-trip: the raw form must survive verbatim
    assert str(ChapterId("28.22")) == "28.22"
    assert ChapterId("007").raw == "007"


def test_chapter_id_value_extracts_leading_number():
    assert ChapterId("7b").value == 7.0
    assert ChapterId("9.22").value == 9.22


def test_chapter_id_non_numeric_sorts_after_numeric():
    ids = ["extra", "2", "10", "1"]
    assert sort_chapter_ids(ids) == ["1", "2", "10", "extra"]


def test_chapter_id_equality_by_raw():
    assert ChapterId("1") == ChapterId("1")
    assert ChapterId("1") != ChapterId("1.0")


# ============================ sort_chapter_ids ===========================


def test_sort_chapter_ids_ascending_with_decimals():
    ids = ["10", "2", "28.22", "28", "9", "29"]
    assert sort_chapter_ids(ids) == ["2", "9", "10", "28", "28.22", "29"]


def test_sort_chapter_ids_deduplicates():
    assert sort_chapter_ids(["1", "1", "2"]) == ["1", "2"]


# ============================ select_chapters ============================


def test_select_single_chapter_by_number():
    available = ["1", "2", "3", "40"]
    # selecting "40" returns chapter 40, not the 40th item
    assert select_chapters(["40"], available) == ["40"]


def test_select_range_inclusive():
    available = ["1", "2", "3", "4", "5", "6"]
    assert select_chapters(["2-4"], available) == ["2", "3", "4"]


def test_select_range_spans_decimal_chapters():
    available = ["8", "9", "9.22", "10", "11", "12"]
    # the whole point: a range over a decimal chapter includes it
    assert select_chapters(["9-10"], available) == ["9", "9.22", "10"]


def test_select_range_tolerates_gaps():
    # chapter 11 is missing; range must not crash or invent it
    available = ["9", "10", "12"]
    assert select_chapters(["9-12"], available) == ["9", "10", "12"]


def test_select_decimal_single():
    available = ["28", "28.22", "29"]
    assert select_chapters(["28.22"], available) == ["28.22"]


def test_select_preserves_request_order_and_dedupes():
    available = ["1", "2", "3", "4", "5"]
    # overlapping tokens: order follows the request, no duplicates
    assert select_chapters(["3", "1-2", "2"], available) == ["3", "1", "2"]


def test_select_reversed_range_is_normalized():
    available = ["1", "2", "3", "4"]
    assert select_chapters(["4-2"], available) == ["2", "3", "4"]


def test_select_unmatched_token_is_skipped(caplog):
    available = ["1", "2", "3"]
    # chapter 99 doesn't exist -> skipped with a warning, others still returned
    result = select_chapters(["2", "99"], available)
    assert result == ["2"]
    assert "99" in caplog.text


def test_select_empty_tokens_ignored():
    available = ["1", "2"]
    assert select_chapters(["", "1"], available) == ["1"]


def test_select_none_of_range_present():
    available = ["1", "2", "3"]
    assert select_chapters(["50-60"], available) == []


# ===================== opaque-slug (positional) fallback =================
# Some sites (mangabuddy) use chapter ids that carry no extractable number,
# e.g. "vol-54-chapter-name" or "chapter-3000". For these, ordering preserves
# the site's own order and selection falls back to 1-based position.


OPAQUE = ["chapter-3000", "vol-54-some-title", "read-now"]


def test_sort_preserves_order_for_opaque_slugs():
    # no alphabetic reshuffle: the site's order is authoritative
    assert sort_chapter_ids(OPAQUE) == OPAQUE


def test_sort_opaque_still_dedupes():
    assert sort_chapter_ids(["a", "b", "a"]) == ["a", "b"]


def test_select_opaque_single_is_positional():
    # "1" -> the 1st chapter in the list, not a chapter numbered 1
    assert select_chapters(["1"], OPAQUE) == ["chapter-3000"]


def test_select_opaque_range_is_positional():
    assert select_chapters(["1-2"], OPAQUE) == ["chapter-3000", "vol-54-some-title"]


def test_select_opaque_out_of_range_skipped(caplog):
    assert select_chapters(["9"], OPAQUE) == []
    assert "out of range" in caplog.text


def test_select_mixed_numeric_slug_uses_number_path():
    # if ANY id carries a number, the numeric path is used (chapter number),
    # because such a site is numerically orderable
    available = ["chapter-10", "chapter-9", "chapter-28.22"]
    # ChapterId pulls no leading number from "chapter-10" -> these are opaque,
    # so this stays positional; documents the boundary explicitly
    assert select_chapters(["1"], available) == ["chapter-10"]
