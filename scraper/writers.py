"""
Volume format writers.

A writer turns a :class:`scraper.manga.Volume` (its ordered pages) into a file
on disk in a particular format (PDF or CBZ). Writers are a separate concern from
download orchestration: ``MangaBuilder`` depends on the ``VolumeWriter``
interface and is handed a concrete writer, rather than owning the format logic
itself.

The ``Volume`` type is only referenced for type-checking; at runtime writers
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
    from scraper.manga import Volume

logger = logging.getLogger(__name__)


class VolumeWriter(Protocol):
    """Writes a volume's pages to ``volume.file_path`` in a given format."""

    extension: str

    def write(self, volume: "Volume") -> None: ...


class PdfWriter:
    """Save a volume's pages to a single PDF file."""

    extension = "pdf"

    def write(self, volume: "Volume") -> None:
        if not volume.pages:
            return None
        logger.info(f"Volume {volume.number} saved to {volume.file_path}")
        # Write to a temp sibling and atomically publish, so a failure midway
        # leaves no partial/corrupt PDF at the final path.
        with atomic_write_path(volume.file_path) as tmp:
            c = canvas.Canvas(str(tmp))
            for page in volume.pages:
                img = BytesIO(page.img)
                cover = Image.open(img)
                width, height = cover.size
                c.setPageSize((width, height))
                imgreader = ImageReader(img)
                c.drawImage(imgreader, x=0, y=0)
                c.showPage()
            c.save()


class CbzWriter:
    """Save a volume's pages to a CBZ (zip) archive.

    The naming schema is important. If too much info is within the jpg file
    name the page order can be read wrong in some CBZ readers. The most reliable
    format is like ``001_1.jpg`` (``<page_num>_<vol_num>.jpg``).

    See forum post for more details: https://tinyurl.com/uu5kvjf
    """

    extension = "cbz"

    def write(self, volume: "Volume") -> None:
        if not volume.pages:
            return None
        logger.info(f"Volume {volume.number} saved to {volume.file_path}")
        # Write to a temp sibling and atomically publish (no partial .cbz on
        # failure); the `with ZipFile` still finalizes the archive on exit.
        with atomic_write_path(volume.file_path) as tmp:
            with zipfile.ZipFile(str(tmp), "w") as cbz:
                for page in volume.pages:
                    jpgfilename = f"{page.number:03d}_{volume.number}.jpg"
                    tmp_jpg = Path(tempfile.gettempdir()) / jpgfilename
                    tmp_jpg.write_bytes(page.img)
                    cbz.write(tmp_jpg, jpgfilename)
                    tmp_jpg.unlink()


_WRITERS: Dict[str, Type[VolumeWriter]] = {
    PdfWriter.extension: PdfWriter,
    CbzWriter.extension: CbzWriter,
}


def get_writer(filetype: str) -> Optional[VolumeWriter]:
    """Return a writer for ``filetype`` (e.g. ``"pdf"``/``"cbz"``), or None."""
    writer_cls = _WRITERS.get(filetype)
    return writer_cls() if writer_cls else None
