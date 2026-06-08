"""
Tests for Bundle's ComicInfo <Writer> source (E1).

Kept hermetic: pass jobs=1 so resolve_jobs() doesn't read settings, and mock
_configured_writer for the fallback case.
"""

from unittest import mock

import pytest

from scraper.bundle import Bundle
from scraper.manga import Manga


def test_bundle_writer_prefers_manga_author():
    # E1: an extracted author becomes the ComicInfo <Writer>.
    manga = Manga("some-series", "cbz", author="Mihachi Kagano")
    bundle = Bundle(manga, chapters_per_volume=1, jobs=1)
    assert bundle.writer == "Mihachi Kagano"


def test_bundle_writer_falls_back_to_configured_default():
    # No author on the manga -> the neutral/ini-configured writer, never a
    # hardcoded person.
    manga = Manga("some-series", "cbz")  # author defaults to None
    with mock.patch("scraper.bundle._configured_writer", return_value="Unknown"):
        bundle = Bundle(manga, chapters_per_volume=1, jobs=1)
    assert bundle.writer == "Unknown"


# ====================== kcc-c2e MOBI conversion (R3) =====================


def _bundle():
    return Bundle(Manga("x", "cbz"), chapters_per_volume=1, jobs=1)


def test_convert_to_mobi_raises_when_kcc_missing():
    bundle = _bundle()
    with mock.patch("scraper.bundle.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="kcc-c2e not found"):
            bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_raises_on_nonzero_exit():
    bundle = _bundle()
    fail = mock.Mock(returncode=1, stderr="kindlegen: not found")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=fail):
            # even if a file somehow exists, a non-zero exit must raise
            with mock.patch("scraper.bundle.os.path.exists", return_value=True):
                with pytest.raises(RuntimeError, match="kcc-c2e failed"):
                    bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_raises_when_output_missing():
    bundle = _bundle()
    ok = mock.Mock(returncode=0, stderr="")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=ok):
            # exit 0 but no MOBI produced -> still an error (don't claim success)
            with mock.patch("scraper.bundle.os.path.exists", return_value=False):
                with pytest.raises(RuntimeError, match="expected"):
                    bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_succeeds_when_output_present():
    bundle = _bundle()
    ok = mock.Mock(returncode=0, stderr="")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=ok) as run:
            with mock.patch("scraper.bundle.os.path.exists", return_value=True):
                bundle._convert_to_mobi("v.cbz", "out/v.mobi")
    # invoked kcc-c2e with the output dir + cbz
    assert run.call_args[0][0][0] == "kcc-c2e"
    assert "out" in run.call_args[0][0]
    assert "v.cbz" in run.call_args[0][0]
