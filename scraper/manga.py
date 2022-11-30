"""
Manga building blocks & factories
"""

import logging
from dataclasses import dataclass, field
from multiprocessing import Lock
from multiprocessing.pool import Pool, ThreadPool
from pathlib import Path
from types import prepare_class
from typing import Callable, Dict, Generator, List, Optional
import zipfile
import tempfile
import tqdm

from scraper.exceptions import (
    PageAlreadyPresent,
    PageDoesNotExist,
    VolumeAlreadyExists,
    VolumeAlreadyPresent,
    VolumeDoesntExist,
)
from scraper.new_types import PageData, VolumeData
from scraper.parsers.types import SiteParser
from scraper.utils import get_adapter, settings



logger = logging.getLogger(__name__)
LOCK = Lock()


@dataclass(frozen=True, repr=False)
class Page:
    """
    Holds page number & its image
    """

    number: int
    img: bytes

    def __repr__(self) -> str:
        return self._str()

    def __str__(self) -> str:
        return self._str()

    def _str(self) -> str:
        img = True if self.img else False
        return f"Page(number={self.number}, img={img})"


@dataclass
class Volume:
    """
    Manga volume & its pages
    """

    number: str
    file_path: Path
    upload_path: Path
    _pages: Dict[int, Page] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return self._str()

    def __str__(self) -> str:
        return self._str()

    def __eq__(self, other):
        attrs = [attr for attr in self.__dict__.keys()]
        return all(
            str(getattr(self, attr)) == str(getattr(other, attr)) for attr in attrs
        )

    def __iter__(self) -> Generator:
        for page in self.pages:
            yield page

    def _str(self) -> str:
        return f"Volume(number={self.number}, pages={len(self.pages)})"

    @property
    def page(self) -> Dict[int, Page]:
        return self._pages

    @property
    def pages(self) -> List[Page]:
        pages = self._pages.values()
        sorted_pages = sorted(pages, key=lambda x: x.number)
        return sorted_pages

    @pages.setter
    def pages(self, metadata: List[PageData]) -> None:
        self._pages = {}
        for page_number, img in metadata:
            self.add_page(page_number, img)

    def add_page(self, page_number: int, img: bytes) -> None:
        """
        Appends a Page object from a page number & its image
        """
        if self.page.get(page_number):
            raise PageAlreadyPresent(f"Page {page_number} is already present")
        page = Page(number=page_number, img=img)
        self._pages[page_number] = page

    def total_pages(self) -> int:
        return max(self.page)


@dataclass
class Manga:
    """
    Manga with volume and pages objects
    """

    name: str
    filetype: str
    _volumes: Dict[str, Volume] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return self._str()

    def __str__(self) -> str:
        return self._str()

    def __iter__(self) -> Generator[Volume, None, None]:
        for volume in self.volumes:
            yield volume

    def _str(self) -> str:
        return f"Manga(name={self.name}, volumes={len(self.volumes)})"

    def _volume_path(self, volume_number: str) -> Path:
        '''Create volume path
        '''
        manga_dir = settings()["config"]["manga_directory"]
        return Path(
            f"{manga_dir}/{self.name}/{self.name}"
            f"_volume_{volume_number}.{self.filetype}"
        )

    def _volume_upload_path(self, volume_number: str) -> Path:
        '''Create upload volume path
        '''
        root = Path(settings()["config"]["upload_root"])
        return root / f"{self.name}/{self.name}_volume_{volume_number}.{self.filetype}"

    @property
    def volume(self) -> Dict[str, Volume]:
        return self._volumes

    @property
    def volumes(self) -> List[Volume]:
        volumes = self._volumes.values()
        sorted_volumes = sorted(volumes, key=lambda x: x.number)
        return sorted_volumes

    @volumes.setter
    def volumes(self, volumes: List[str]) -> None:
        self._volumes = {}
        for volume in volumes:
            self.add_volume(volume)

    def add_volume(self, volume_number: str, complete=True) -> None:
        if self.volume.get(volume_number):
            raise VolumeAlreadyPresent(f"Volume {volume_number} is already present")

        vol_str = volume_number
        if not complete:
            vol_str += '-incomplete'
        vol_path = self._volume_path(vol_str)
        vol_upload_path = self._volume_upload_path(vol_str)
        volume = Volume(
            number=volume_number, file_path=vol_path, upload_path=vol_upload_path
        )
        self._volumes[volume.number] = volume
        if vol_path.exists():
            logger.info(f"Volume {volume_number} already saved to disk")


    def volume_exists(self, volume_number: str) -> None:
        vol_path = self._volume_path(volume_number)
        if vol_path.exists():
            logger.info(f"Volume {volume_number} already exists in {vol_path}")
            return True
        else:
            return False


class MangaBuilder:
    """
    Creates Manga objects
    """

    def __init__(self, parser: SiteParser, filetype=None) -> None:
        self.parser: SiteParser = parser
        self.adapter = get_adapter(logger, self.parser.manga.name)
        self.type = filetype
        self.manga = None

    def _get_volume_data(self, volume_number: str) -> VolumeData:
        """
        Download pages of a volume, and save them to disk (in pdf or cbz)
        Returns volume number & each pages raw data
        """
        assert(self.manga)

        # On windows, sub-process do not inherit logLevel
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(process)s %(levelname)s %(message)s',
        )

        self.adapter.info(f"Downloading volume {volume_number}")
        try:
            urls = self.parser.manga.page_urls(volume_number)
            #self.adapter.info(f"urls={[url for url in urls]}")
        except VolumeDoesntExist as e:
            self.adapter.error(e)
            return (volume_number, None)
        if urls:
            with ThreadPool() as pool:
                # Download the data from urls
                # i.e download the volume images in parallel threads
                pages_data = pool.map(self.parser.manga.page_data, urls)

            if not pages_data:
                self.adapter.error(f"No data for volume {volume_number}")
                return (volume_number, None)

            # check if any page is missing
            volume_complete = True
            if any([not page[1] for page in pages_data]):
                self.adapter.error(f"Volume {volume_number} is missing pages {','.join([str(page[0]) for page in pages_data if not page[1]])}")
                volume_complete = False

            # Save the volume to disk
            self.adapter.info(f"Saving volume {volume_number}")
            try:
                LOCK.acquire()
                self.manga.add_volume(volume_number, volume_complete)
                # properties cause an error in mypy when getter/setters input
                # differ, mypy thinks they should be the same
                self.manga.volume[volume_number].pages = pages_data  # type: ignore
                LOCK.release()
            except (VolumeAlreadyExists, VolumeAlreadyPresent) as e:
                self.adapter.error(e)
            self._create_manga_dir(self.manga.name)

            save_method = self._get_save_method(self.type)
            save_method(self.manga.volume[volume_number])
            self.adapter.info(f"Volume {volume_number} done")

            return (volume_number, None)
        return None

    def _get_volumes_data(
        self, vol_nums: List[str] = []
    ) -> List[VolumeData]:
        """
        Download a list of volumes
        Each volume is processed in parallel processes
        Returns list of raw volume data
        """
        self.adapter.info(f"Downloading volumes data...")
        # self.adapter.info(f"[MangaBuilder:_get_volumes_data] self.manga.name={self.manga.name}")
        with Pool() as pool:
            return list(tqdm.tqdm(pool.imap(self._get_volume_data, vol_nums), total=len(vol_nums)))


    def _create_manga_dir(self, manga_name: str) -> None:
        """
        Create a manga directory if it does not exist.
        """
        download_dir = settings()["config"]["manga_directory"]
        manga_dir = Path(download_dir) / manga_name
        manga_dir.mkdir(parents=True, exist_ok=True)

    def _to_pdf(self, volume: Volume) -> None:
        """
        Save all pages to a PDF file
        """
        if not volume.pages:
            return None
        self.adapter.info(f"Volume {volume.number} saved to {volume.file_path}")
        c = canvas.Canvas(str(volume.file_path))
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

        The naming schema is important. If too much info is
        within the jpg file name the page order can be read
        wrong in some CBZ readers. The most reliable format is
        like 001_1.jpg (<pag_num>_<vol_num>.jpg).

        See forum post for more details:
            https://tinyurl.com/uu5kvjf
        """
        if not volume.pages:
            return None
        self.adapter.info(f"Volume {volume.number} saved to {volume.file_path}")
        with zipfile.ZipFile(str(volume.file_path), "w") as cbz:
            for page in volume.pages:
                jpgfilename = f"{page.number:03d}_{volume.number}.jpg"
                tmp_jpg = Path(tempfile.gettempdir()) / jpgfilename
                tmp_jpg.write_bytes(page.img)
                cbz.write(tmp_jpg, jpgfilename)
                tmp_jpg.unlink()

    def _get_save_method(self, filetype) -> Callable:
        """
        Returns the appropriate image conversion method.
        """
        conversion_method = {"pdf": self._to_pdf, "cbz": self._to_cbz}
        return conversion_method.get(filetype)

    def get_manga_volumes(
        self,
        vol_nums: Optional[List[str]] = None,
        filetype: str = "cbz",
        preferred_name: Optional[str] = None,
    ) -> Manga:
        """
        Returns a Manga object containing the requested volumes
        """
        preferred_name = preferred_name if preferred_name else self.parser.manga.name
        preferred_name = preferred_name.replace(':', '')
        self.adapter.info(f"[MangaBuilder:get_manga_volumes] manga_name={self.parser.manga.name}, preferred_name={preferred_name}")
        # Create a Manga instance
        self.manga = Manga(preferred_name, filetype)
        # Find the list of volumes for that manga
        vol_nums = self.parser.manga.all_volume_numbers() if not vol_nums else vol_nums
        # Filter out volumes already saved to disk
        vol_nums = [vol for vol in vol_nums if not self.manga.volume_exists(vol)]
        self.adapter.info(f'vol_nums={vol_nums}')

        # Download the volumes
        self._get_volumes_data(vol_nums)

        return self.manga
