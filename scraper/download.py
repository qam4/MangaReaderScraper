"""
Downloads manga page images
"""

import logging
import tempfile
import zipfile
from io import BytesIO
from logging import LoggerAdapter
from multiprocessing.pool import Pool
from pathlib import Path
from typing import Callable, List, Optional

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from scraper.manga import MangaBuilder, Volume
from scraper.parsers.base import BaseMangaParser
from scraper.utils import download_timer, get_adapter, settings

logger = logging.getLogger(__name__)

MANGA_DIR = settings()["manga_directory"]


class Download:
    """
    Downloads the manga in the desired format
    """

    def __init__(self, manga_name: str, filetype: str, parser: BaseMangaParser) -> None:
        self.manga_name: str = manga_name
        self.factory: MangaBuilder = MangaBuilder(parser=parser(manga_name))
        self.adapter: LoggerAdapter = get_adapter(logger, manga_name)
        self.type: str = filetype

    def _create_manga_dir(self, manga_name: str) -> None:
        """
        Create a manga directory if it does not exist.
        """
        manga_dir = Path(MANGA_DIR) / manga_name
        manga_dir.mkdir(parents=True, exist_ok=True)

    def _to_pdf(self, volume: Volume) -> None:
        """
        Save all pages to a PDF file
        """
        self.adapter.info(f"volume saved to {volume.file_path}")
        c = canvas.Canvas(volume.file_path)
        for page in volume.pages:
            img = BytesIO(page.img)
            cover = Image.open(img)
            width, height = cover.size
            c.setPageSize((width, height))
            imgreader = ImageReader(img)
            c.drawImage(imgreader, x=0, y=0)
            c.showPage()
        c.save()

    def _to_cbz(self, volume: Volume) -> None:
        """
        Save all pages to a CBZ file
        """
        self.adapter.info(f"volume saved to {volume.file_path}")
        with zipfile.ZipFile(volume.file_path, "w") as cbz:
            for num, page in enumerate(volume.pages):
                jpgfilename = f"{self.manga_name}_{volume.number}_page_{num}.jpg"
                tmp_jpg = Path(tempfile.gettempdir()) / jpgfilename
                tmp_jpg.write_bytes(page.img)
                cbz.write(tmp_jpg)
                tmp_jpg.unlink()

    def _get_save_method(self) -> Callable:
        """
        Returns the appropriate image conversion method.
        """
        conversion_method = {"pdf": self._to_pdf, "cbz": self._to_cbz}
        return conversion_method.get(self.type)

    @download_timer
    def download_volumes(self, vol_nums: Optional[List[int]] = None) -> None:
        """
        Download all pages and volumes
        """
        self.adapter.info(f"Starting Downloads")
        manga = self.factory.get_manga_volumes(vol_nums, self.type)
        self._create_manga_dir(manga.name)
        save_method = self._get_save_method()
        with Pool() as pool:
            pool.map(save_method, manga.volumes)
        self.adapter.info(f"All volumes downloaded")


def download_manga(
    manga_name: str, volumes: Optional[int], filetype: str, parser: BaseMangaParser
) -> None:
    downloader = Download(manga_name, filetype, parser)
    downloader.download_volumes(volumes)
