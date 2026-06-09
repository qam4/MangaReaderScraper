"""
Chapter format writers.

A writer turns a :class:`scraper.manga.Chapter` (its ordered pages) into a file
on disk in a particular format (PDF or CBZ). Writers are a separate concern from
download orchestration: ``MangaBuilder`` depends on the ``ChapterWriter``
interface and is handed a concrete writer, rather than owning the format logic
itself.

The ``Chapter`` type is only referenced for type-checking; at runtime writers
duck-type the attributes they need (``pages``, ``number``, ``file_path``), which
also avoids a circular import with ``scraper.manga``.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Protocol, Type

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from scraper.utils import atomic_write_path

if TYPE_CHECKING:
    from scraper.manga import Chapter

logger = logging.getLogger(__name__)


class ChapterWriter(Protocol):
    """Writes a chapter's pages to ``chapter.file_path`` in a given format."""

    extension: str

    def write(self, chapter: "Chapter") -> None: ...


class PdfWriter:
    """Save a chapter's pages to a single PDF file."""

    extension = "pdf"

    def write(self, chapter: "Chapter") -> None:
        if not chapter.pages:
            return None
        logger.info(f"Chapter {chapter.number} saved to {chapter.file_path}")
        # Write to a temp sibling and atomically publish, so a failure midway
        # leaves no partial/corrupt PDF at the final path.
        with atomic_write_path(chapter.file_path) as tmp:
            c = canvas.Canvas(str(tmp))
            for page in chapter.pages:
                img = BytesIO(page.img)
                cover = Image.open(img)
                width, height = cover.size
                c.setPageSize((width, height))
                imgreader = ImageReader(img)
                c.drawImage(imgreader, x=0, y=0)
                c.showPage()
            c.save()


class CbzWriter:
    """Save a chapter's pages to a CBZ (zip) archive.

    The naming schema is important. If too much info is within the jpg file
    name the page order can be read wrong in some CBZ readers. The most reliable
    format is like ``001_1.jpg`` (``<page_num>_<chap_num>.jpg``).

    See forum post for more details: https://tinyurl.com/uu5kvjf
    """

    extension = "cbz"

    def write(self, chapter: "Chapter") -> None:
        if not chapter.pages:
            return None
        logger.info(f"Chapter {chapter.number} saved to {chapter.file_path}")
        # Write to a temp sibling and atomically publish (no partial .cbz on
        # failure); the `with ZipFile` still finalizes the archive on exit.
        with atomic_write_path(chapter.file_path) as tmp:
            with zipfile.ZipFile(str(tmp), "w") as cbz:
                for page in chapter.pages:
                    jpgfilename = f"{page.number:03d}_{chapter.number}.jpg"
                    tmp_jpg = Path(tempfile.gettempdir()) / jpgfilename
                    tmp_jpg.write_bytes(page.img)
                    cbz.write(tmp_jpg, jpgfilename)
                    tmp_jpg.unlink()


_WRITERS: Dict[str, Type[ChapterWriter]] = {
    PdfWriter.extension: PdfWriter,
    CbzWriter.extension: CbzWriter,
}


def get_writer(filetype: str) -> Optional[ChapterWriter]:
    """Return a writer for ``filetype`` (e.g. ``"pdf"``/``"cbz"``), or None."""
    writer_cls = _WRITERS.get(filetype)
    return writer_cls() if writer_cls else None
