from pathlib import Path
from unittest import mock

import pytest

from scraper.manga import Chapter
from scraper.writers import CbzWriter, PdfWriter, get_writer

JPG = Path("tests/test_files/jpgs/test-manga_1_1.jpg").read_bytes()
JPG2 = Path("tests/test_files/jpgs/test-manga_1_2.jpg").read_bytes()


def _chapter(file_path: Path) -> Chapter:
    chapter = Chapter("1", file_path, file_path)
    chapter.pages = [(1, JPG, "success"), (2, JPG2, "success")]
    return chapter


def test_cbz_writer_leaves_no_file_when_write_fails(tmp_path):
    # atomic write: if zipping raises midway, no partial/corrupt .cbz is left at
    # the final path (the bug class behind unreadable cbz archives).
    out = tmp_path / "chapter_1.cbz"
    chapter = _chapter(out)
    with mock.patch(
        "scraper.writers.zipfile.ZipFile", side_effect=RuntimeError("disk full")
    ):
        with pytest.raises(RuntimeError):
            CbzWriter().write(chapter)
    assert not out.exists()
    assert not out.with_name(out.name + ".part").exists()


def test_get_writer_returns_matching_writer():
    assert isinstance(get_writer("pdf"), PdfWriter)
    assert isinstance(get_writer("cbz"), CbzWriter)


def test_get_writer_unknown_filetype_is_none():
    assert get_writer("epub") is None


def test_pdf_writer_writes_pdf_signature(tmp_path):
    out = tmp_path / "chapter_1.pdf"
    PdfWriter().write(_chapter(out))
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF-")


def test_cbz_writer_writes_zip_signature_and_page_entries(tmp_path):
    import zipfile

    out = tmp_path / "chapter_1.cbz"
    CbzWriter().write(_chapter(out))
    assert out.exists()
    assert out.read_bytes().startswith(b"PK")
    with zipfile.ZipFile(out) as cbz:
        # naming schema is <page_num zero-padded>_<chap_num>.jpg, in page order
        assert cbz.namelist() == ["001_1.jpg", "002_1.jpg"]


def test_writer_with_no_pages_writes_nothing(tmp_path):
    out = tmp_path / "empty.pdf"
    PdfWriter().write(Chapter("1", out, out))
    assert not out.exists()
