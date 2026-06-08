"""
Manga building blocks & factories
"""

import logging
from dataclasses import dataclass, field
from multiprocessing.pool import Pool, ThreadPool
from pathlib import Path
from typing import Dict, Generator, Iterable, List, Optional

from scraper.exceptions import (
    PageAlreadyPresent,
    # PageDoesNotExist,
    # VolumeAlreadyExists,
    VolumeAlreadyPresent,
    VolumeDoesntExist,
)
from scraper.new_types import PageData
from scraper.parsers.types import SiteParser
from scraper.selection import ChapterId, select_chapters, sort_chapter_ids
from scraper.utils import (
    configure_logging,
    get_adapter,
    get_console,
    resolve_jobs,
    settings,
)
from scraper.writers import get_writer

logger = logging.getLogger(__name__)


@dataclass
class VolumeDownload:
    """Result of one worker downloading a volume -- the value that crosses the
    multiprocess boundary.

    The worker is side-effect-free w.r.t. shared state (it does not mutate the
    builder's Manga or write to disk); it returns this, and the parent assembles
    the Manga and writes the file from it. ``pages`` is ``None`` for a skipped
    volume (already on disk / no pages / chapter doesn't exist); ``complete`` is
    False when pages are missing or the volume couldn't be fetched.
    """

    volume_id: str
    volume_index: int
    pages: Optional[List[PageData]] = None
    complete: bool = True


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
        """Number of pages in the volume (a count, not the max page number)."""
        return len(self._pages)


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
            f"{manga_dir}/{self.name}/{self.name}_chapter_{volume_id}.{self.filetype}"
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
        sorted_volumes = sorted(volumes, key=lambda v: ChapterId(v.number))
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

    def __init__(
        self, parser: SiteParser, filetype="pdf", jobs: Optional[int] = None
    ) -> None:
        self.parser: SiteParser = parser
        self.adapter = get_adapter(logger, self.parser.manga.manga_url)
        self.type: str = filetype
        self.writer = get_writer(filetype)
        self.jobs: int = resolve_jobs(jobs)
        self.manga: Optional[Manga] = None

    def _get_volume_data_wrapped(self, arg):
        return self._download_volume(*arg)  # Unpacks (index, volume_id, on_disk)

    def _download_volume(
        self, volume_index: int, volume_id: str, already_on_disk: bool
    ) -> "VolumeDownload":
        """
        Download every page of one volume and RETURN the result. Hermetic: it
        does NOT mutate ``self.manga``, does NOT write to disk, and does NOT read
        ``settings()`` -- so it behaves identically in a spawned worker process,
        where module-level config/patches don't propagate.

        This is the multiprocess worker. Under a spawn ``Pool`` the child runs on
        a private copy of the builder, so any mutation here would be discarded
        when the process ends -- only the return value crosses the boundary. The
        parent decides ``already_on_disk`` (it owns config/disk access) and owns
        Manga assembly + writing in ``_add_download_to_manga``.

        ``pages`` is ``None`` for a volume that was skipped -- already complete on
        disk, no page urls, or the chapter doesn't exist.
        """
        if already_on_disk:
            return VolumeDownload(volume_id, volume_index, pages=None, complete=True)

        self.adapter.info(
            f"Downloading volume {volume_index} from {self.parser.manga.volume_url(volume_id)}"
        )
        try:
            urls = self.parser.manga.page_urls(volume_id)
        except VolumeDoesntExist as e:
            self.adapter.error(e)
            return VolumeDownload(volume_id, volume_index, pages=None, complete=False)
        if not urls:
            self.adapter.warning(f"No pages found for volume {volume_id}, skipping")
            return VolumeDownload(volume_id, volume_index, pages=None, complete=False)

        with ThreadPool() as pool:
            # download the volume images in parallel threads
            pages_data = list(pool.map(self.parser.manga.page_data, urls))

        if not pages_data:
            self.adapter.error(f"No data for volume {volume_id}")
            return VolumeDownload(volume_id, volume_index, pages=None, complete=False)

        # flag a volume that is missing pages (any page that didn't 'success')
        missing = [page for page in pages_data if page[2] != "success"]
        complete = not missing
        if missing:
            self.adapter.error(
                f"Volume {volume_id} is missing pages "
                f"{','.join(str(page[0]) for page in missing)}, "
                f"url={self.parser.manga.volume_url(volume_id)}"
            )

        self.adapter.info(f"Volume {volume_id} done")
        return VolumeDownload(
            volume_id, volume_index, pages=pages_data, complete=complete
        )

    def _get_volumes_data(self, vol_ids: Iterable[str] = []) -> List["VolumeDownload"]:
        """
        Download a list of volumes, each in its own worker process, and return
        the per-volume :class:`VolumeDownload` results (in input order).

        The parent decides up front which volumes are already on disk (the only
        config/disk-dependent step) and passes that in, so the workers stay
        hermetic. The parent assembles the Manga from these returns.
        """
        assert self.manga is not None
        vol_ids = list(vol_ids)
        # Parent-side (has settings/disk access): which volumes are already saved?
        worker_args = [
            (index, volume_id, self.manga.volume_exists(volume_id, index))
            for index, volume_id in enumerate(vol_ids, start=1)
        ]
        self.adapter.info("Downloading volumes data...")
        self.adapter.debug(f"self.manga.name={self.manga.name}")

        # rich.progress.Progress shares the same Console as the logging
        # RichHandler (see utils.get_console), so rich coordinates ONE Live
        # region: log lines scroll above while this bar stays pinned at the
        # bottom. (The old tqdm.rich bar had its own Live and fought the handler.)
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        results: List[VolumeDownload] = []
        with Pool(self.jobs, initializer=configure_logging) as pool:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=get_console(),
                transient=False,
            ) as progress:
                task = progress.add_task("Downloading volumes", total=len(worker_args))
                for result in pool.imap(self._get_volume_data_wrapped, worker_args):
                    results.append(result)
                    progress.advance(task)
        return results

    def _create_manga_dir(self, manga_name: str) -> None:
        """
        Create a manga directory if it does not exist.
        """
        download_dir = settings()["config"]["manga_directory"]
        manga_dir = Path(download_dir) / manga_name
        manga_dir.mkdir(parents=True, exist_ok=True)

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
        # Find the list of volumes for that manga, in canonical chapter order
        all_volume_ids = sort_chapter_ids(self.parser.manga.all_volume_ids())

        if not all_volume_ids:
            raise Exception("Empty volumes list")

        # Selection is chapter-number based (see scraper.selection): vol_ids are
        # selector tokens like ["9-12", "28.22"], matched against the chapter
        # numbers the site offers -- not 1-based indices into the list.
        if vol_ids is None:
            vol_ids = list(all_volume_ids)
        else:
            vol_ids = select_chapters(vol_ids, all_volume_ids)
        self.adapter.debug(f"vol_ids={vol_ids}")

        # Download the volumes in parallel worker processes. Each worker returns
        # a VolumeDownload (it does NOT touch self.manga or disk -- child-side
        # mutations don't survive a spawn Pool). The PARENT owns assembly +
        # writing below, so the in-memory Manga is correct after a real run.
        downloads = self._get_volumes_data(vol_ids)

        for download in downloads:
            self._add_download_to_manga(download)

        return self.manga

    def _add_download_to_manga(self, download: "VolumeDownload") -> None:
        """Assemble one worker result into ``self.manga`` and write it to disk.

        Parent-side: registers the volume, populates its pages from the worker's
        RETURN value (the only thing that survives the process boundary), and
        saves it via the writer. A skipped volume (``pages is None`` -- already
        on disk / no pages) is still listed as metadata but written nothing.
        """
        assert self.manga is not None
        volume_id = download.volume_id
        if not self.manga.volumes_dict.get(volume_id):
            self.manga.add_volume(
                volume_id,
                volume_index=download.volume_index,
                complete=download.complete,
            )

        if download.pages is None:
            return

        volume = self.manga.volumes_dict[volume_id]
        if not volume.pages:
            volume.pages = list(download.pages)  # type: ignore[assignment]

        # Persist the assembled volume (parent-side, once -- not in the worker).
        self._create_manga_dir(self.manga.name)
        if self.writer:
            self.adapter.info(f"Saving volume {volume_id}")
            self.writer.write(volume)

        # If this volume is complete, remove any stale "-incomplete" file left by
        # an earlier partial run. volume_exists only ever checks the COMPLETE
        # name, so without this the orphaned "<...>-incomplete.<ext>" lingers
        # forever (and you can end up with both variants side by side).
        if download.complete:
            self._remove_incomplete_sibling(volume.file_path)

    @staticmethod
    def _incomplete_path(complete_path: Path) -> Path:
        """The ``-incomplete`` filename variant of a complete volume path.

        ``foo_chapter_1_1.pdf`` -> ``foo_chapter_1_1-incomplete.pdf`` (mirrors how
        ``Manga.add_volume`` appends ``-incomplete`` before the extension).
        """
        return complete_path.with_name(
            f"{complete_path.stem}-incomplete{complete_path.suffix}"
        )

    def _remove_incomplete_sibling(self, complete_path: Path) -> None:
        """Delete a leftover ``-incomplete`` file for a now-complete volume."""
        incomplete = self._incomplete_path(complete_path)
        if incomplete.exists():
            self.adapter.info(f"Removing stale incomplete file {incomplete}")
            try:
                incomplete.unlink()
            except OSError as err:  # pragma: no cover - unexpected fs error
                self.adapter.warning(f"Could not remove {incomplete}: {err}")
