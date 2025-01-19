"""
Manga building blocks & factories
"""

import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from multiprocessing.pool import Pool, ThreadPool
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, List, Optional, Tuple
import tqdm  # type: ignore
from tqdm.contrib.logging import logging_redirect_tqdm  # type: ignore
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from scraper.exceptions import (
    PageAlreadyPresent,
    # PageDoesNotExist,
    # VolumeAlreadyExists,
    VolumeAlreadyPresent,
    VolumeDoesntExist,
)
from scraper.new_types import PageData, VolumeData
from scraper.parsers.types import SiteParser
from scraper.utils import get_adapter, settings


logger = logging.getLogger(__name__)


def natural_sort(_list, key=lambda s: s) -> List[Any]:
    """
    Sort the list into natural alphanumeric order.
    """

    def convert_text(text: str):
        return int(text) if text.isdigit() else text.lower()

    def get_alphanum_key_func(key):
        return lambda s: [convert_text(c) for c in re.split("([0-9]+)", key(s))]

    sort_key = get_alphanum_key_func(key)
    return sorted(_list, key=sort_key)


def sanitize_filename(filename: str) -> str:
    """
    Convert a string to a safe filename
    """
    keepcharacters = (" ", ".", "_", "-")
    return "".join(c for c in filename if c.isalnum() or c in keepcharacters).rstrip()


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
        return f"Volume(number={self.number}, file_path={self.file_path}, upload_path={self.upload_path}, pages={len(self.pages)})"

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
        for page_number, img, _ in metadata:
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

    def _volume_path(self, volume_id: str) -> Path:
        """Create volume path"""
        manga_dir = settings()["config"]["manga_directory"]
        return Path(
            f"{manga_dir}/{self.name}/{self.name}"
            f"_chapter_{volume_id}.{self.filetype}"
        )

    def _volume_upload_path(self, volume_id: str) -> Path:
        """Create upload volume path"""
        root = Path(settings()["config"]["upload_root"])
        return root / f"{self.name}/{self.name}_chapter_{volume_id}.{self.filetype}"

    @property
    def volumes_dict(self) -> Dict[str, Volume]:
        return self._volumes

    @property
    def volumes(self) -> List[Volume]:
        volumes = self._volumes.values()
        sorted_volumes = natural_sort(volumes, key=lambda x: x.number)
        return sorted_volumes

    @volumes.setter
    # only used in unit tests
    def volumes(self, volumes: List[str]) -> None:
        self._volumes = {}
        for volume in volumes:
            self.add_volume(volume)

    def add_volume(
        self,
        volume_id: str,
        volume_index: Optional[int] = None,
        complete: Optional[bool] = True,
    ) -> None:
        if self.volumes_dict.get(volume_id):
            raise VolumeAlreadyPresent(f"Volume {volume_id} is already present")

        if volume_index is not None:
            vol_path_str = str(volume_index) + "_" + volume_id
        else:
            vol_path_str = volume_id
        if not complete:
            vol_path_str += "-incomplete"
        vol_path = self._volume_path(vol_path_str)
        vol_upload_path = self._volume_upload_path(vol_path_str)
        volume = Volume(
            number=str(volume_index) if volume_index is not None else volume_id,
            file_path=vol_path,
            upload_path=vol_upload_path,
        )
        self._volumes[volume_id] = volume

    def volume_exists(self, volume_id: str, volume_index: int) -> bool:
        if volume_index:
            vol_path_str = str(volume_index) + "_" + volume_id
        else:
            vol_path_str = volume_id
        vol_path = self._volume_path(vol_path_str)
        if vol_path.exists():
            logger.info(f"Volume {vol_path_str} already exists: {vol_path}")
            return True
        else:
            return False


class MangaBuilder:
    """
    Creates Manga objects
    """

    def __init__(self, parser: SiteParser, filetype="pdf") -> None:
        self.parser: SiteParser = parser
        self.adapter = get_adapter(logger, self.parser.manga.manga_url)
        self.type: str = filetype
        self.manga: Optional[Manga] = None

    def _get_volume_data_wrapped(self, arg):
        return self._get_volume_data(*arg)  # Unpacks args

    def _get_volume_data(
        self, volume_index: int, volume_id: str
    ) -> Optional[Tuple[str, Optional[VolumeData]]]:
        """
        Download pages of a volume, and save them to disk (in pdf or cbz)
        Returns volume number & each pages raw data
        """
        # On windows, sub-process do not inherit logLevel, ...
        # also, logs in sub-process mess tqmd (so better keep level=WARN)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s.%(msecs)03d %(levelname)s [%(module)s:%(funcName)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Do not try to download volume data if the complete volume is already saved on disk
        if self.manga.volume_exists(volume_id, volume_index):
            return None

        self.adapter.info(
            f"Downloading volume {volume_index} from {self.parser.manga.volume_url(volume_id)}"
        )
        try:
            urls = self.parser.manga.page_urls(volume_id)
        except VolumeDoesntExist as e:
            self.adapter.error(e)
            return (volume_id, None)
        if urls:
            with ThreadPool() as pool:
                # Download the data from urls
                # i.e download the volume images in parallel threads
                pages_data = pool.map(self.parser.manga.page_data, urls)
            # no multi-thread version:
            # pages_data = list(map(self.parser.manga.page_data, urls))

            if not pages_data:
                self.adapter.error(f"No data for volume {volume_id}")
                return (volume_id, None)

            # check if any page is missing
            volume_complete = True
            if any([page[2] != "success" for page in pages_data]):
                self.adapter.error(
                    f"Volume {volume_id} is missing pages {','.join([str(page[0]) for page in pages_data if page[2] != 'success'])}, url={self.parser.manga.volume_url(volume_id)}"
                )
                volume_complete = False

            # Add the volume to the manga
            # note: each volume is created in its own process,
            # so self.manga of the parent process is not changed
            try:
                self.manga.add_volume(
                    volume_id, volume_index=volume_index, complete=volume_complete
                )
                # properties cause an error in mypy when getter/setters input
                # differ, mypy thinks they should be the same
                self.manga.volumes_dict[volume_id].pages = pages_data  # type: ignore
            except VolumeAlreadyPresent as e:
                self.adapter.error(e)

            # Save the volume to disk
            self.adapter.info(f"Saving volume {volume_id}")
            self._create_manga_dir(self.manga.name)

            save_method = self._get_save_method(self.type)
            if save_method:
                save_method(self.manga.volumes_dict[volume_id])
            self.adapter.info(f"Volume {volume_id} done")

            return (volume_id, None)
        return None

    def _get_volumes_data(self, vol_ids: Iterable[str] = []) -> List[VolumeData]:
        """
        Download a list of volumes
        Each volume is processed in parallel processes
        Returns list of raw volume data
        """
        self.adapter.info("Downloading volumes data...")
        self.adapter.debug(f"self.manga.name={self.manga.name}")
        with logging_redirect_tqdm(loggers=[self.adapter.logger]):
            with Pool(4) as pool:
                volumes_data = list(
                    tqdm.tqdm(
                        pool.imap(
                            self._get_volume_data_wrapped, enumerate(vol_ids, start=1)
                        ),
                        total=len(list(vol_ids)),
                        unit="volumes",
                    )
                )
                return volumes_data
        # no multi-process version:
        # return list(tqdm.tqdm(map(self._get_volume_data, vol_ids), total=len(vol_ids)))

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
        vol_ids: Optional[Iterable[str]] = None,
        title: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> Manga:
        """
        Returns a Manga object containing the requested volumes
        """
        preferred_name = (
            preferred_name
            if preferred_name
            else title
            if title
            else self.parser.manga.manga_url
        )
        preferred_name = sanitize_filename(preferred_name)
        self.adapter.debug(
            f"title={title}, manga_url={self.parser.manga.manga_url}, preferred_name={preferred_name}"
        )
        # Create a Manga instance
        self.manga = Manga(preferred_name, self.type)
        # Find the list of volumes for that manga
        all_volume_ids = self.parser.manga.all_volume_ids()
        # [fm] [all_volumes_numbers[int(i) + 1] for i in vol_ids]
        vol_ids = all_volume_ids if vol_ids is None else vol_ids
        self.adapter.debug(f"vol_ids={vol_ids}")

        # Download the volumes
        _ = self._get_volumes_data(vol_ids)

        # Add volumes to manga
        for index, volume_id in enumerate(vol_ids, start=1):
            # this if statement is needed for unit tests which are not multithreaded
            # to make sure we do not get VolumeAlreadyPresent
            if not self.manga.volumes_dict.get(volume_id):
                volume_complete = self.manga.volume_exists(volume_id, index)
                self.manga.add_volume(
                    volume_id, volume_index=index, complete=volume_complete
                )

        return self.manga
