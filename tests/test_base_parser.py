"""
Tests for base-parser helpers that aren't tied to a specific site.
"""

from unittest import mock

from scraper.parsers.base import _placeholder_font


def test_placeholder_font_uses_arial_when_available():
    sentinel = object()
    with mock.patch(
        "scraper.parsers.base.ImageFont.truetype", return_value=sentinel
    ) as tt:
        assert _placeholder_font(20) is sentinel
    tt.assert_called_once_with("arial.ttf", 20)


def test_placeholder_font_falls_back_when_arial_missing():
    # arial.ttf is Windows-only; on Linux/macOS/CI truetype raises OSError.
    # create_page runs on the download-FAILURE path, so it must never raise --
    # it falls back to Pillow's bundled default font.
    sentinel = object()
    with mock.patch(
        "scraper.parsers.base.ImageFont.truetype", side_effect=OSError("no arial")
    ), mock.patch(
        "scraper.parsers.base.ImageFont.load_default", return_value=sentinel
    ) as default:
        assert _placeholder_font(20) is sentinel
    default.assert_called_once()
