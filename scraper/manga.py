"""
Manga building blocks & factories
"""

import logging
from dataclasses import dataclass, field
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Dict, Generator, Iterable, List, Optional

from scraper.exceptions import (
    # PageDoesNotExist,
    # ChapterAlreadyExists,
    ChapterAlreadyPresent,
    ChapterDoesntExist,
    PageAlreadyPresent,
)
from scraper.new_types import PageData
from scraper.parsers.types import SiteParser
from scraper.selection import ChapterId, select_chapters, sort_chapter_ids
from scraper.utils import (
    get_adapter,
    get_console,
    resolve_jobs,
    settings,
)
from scraper.writers import get_writer

logger = logging.getLogger(__name__)


@dataclass
class ChapterDownload:
    """Result of one worker downloading a chapter -- the value that crosses the
    multiprocess boundary.

    The worker is side-effect-free w.r.t. shared state (it does not mutate the
    builder's Manga or write to disk); it returns this, and the parent assembles
    the Manga and writes the file from it. ``pages`` is ``None`` for a skipped
    chapter (already on disk / no pages / chapter doesn't exist); ``complete`` is
    False when pages are missing or the chapter couldn't be fetched.
    """

    chapter_id: str
    chapter_index: int
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
class Chapter:
    """
    Manga chapter & its pages
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
        return f"Chapter(number={self.number}, file_path={self.file_path}, upload_path={self.upload_path}, pages={len(self.pages)})"

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
        """Number of pages in the chapter (a count, not the max page number)."""
        return len(self._pages)


@dataclass
class Manga:
    """
    Manga with chapter and pages objects
    """

    name: str
    filetype: str
    # Optional author(s) for ComicInfo <Writer>; set parent-side by MangaBuilder
    # from the parser's author() hook. None when the site/parser doesn't expose
    # one (most don't) -> bundle falls back to a neutral default.
    author: Optional[str] = None
    _chapters: Dict[str, Chapter] = field(default_factory=dict, repr=False)

    def __repr__(self) -> str:
        return self._str()

    def __str__(self) -> str:
        return self._str()

    def __iter__(self) -> Generator[Chapter, None, None]:
        for chapter in self.chapters:
            yield chapter

    def _str(self) -> str:
        return f"Manga(name={self.name}, chapters={len(self.chapters)})"

    def _chapter_path(self, chapter_id: str) -> Path:
        """Create chapter path"""
        manga_dir = settings()["config"]["manga_directory"]
        return Path(
            f"{manga_dir}/{self.name}/{self.name}_chapter_{chapter_id}.{self.filetype}"
        )

    def _chapter_upload_path(self, chapter_id: str) -> Path:
        """Create upload chapter path"""
        root = Path(settings()["config"]["upload_root"])
        return root / f"{self.name}/{self.name}_chapter_{chapter_id}.{self.filetype}"

    @property
    def chapters_dict(self) -> Dict[str, Chapter]:
        return self._chapters

    @property
    def chapters(self) -> List[Chapter]:
        chapters = self._chapters.values()
        sorted_chapters = sorted(chapters, key=lambda v: ChapterId(v.number))
        return sorted_chapters

    @chapters.setter
    # only used in unit tests
    def chapters(self, chapters: List[str]) -> None:
        self._chapters = {}
        for chapter in chapters:
            self.add_chapter(chapter)

    def add_chapter(
        self,
        chapter_id: str,
        chapter_index: Optional[int] = None,
        complete: Optional[bool] = True,
    ) -> None:
        if self.chapters_dict.get(chapter_id):
            raise ChapterAlreadyPresent(f"Chapter {chapter_id} is already present")

        if chapter_index is not None:
            chapter_path_str = str(chapter_index) + "_" + chapter_id
        else:
            chapter_path_str = chapter_id
        if not complete:
            chapter_path_str += "-incomplete"
        chapter_path = self._chapter_path(chapter_path_str)
        chapter_upload_path = self._chapter_upload_path(chapter_path_str)
        chapter = Chapter(
            number=str(chapter_index) if chapter_index is not None else chapter_id,
            file_path=chapter_path,
            upload_path=chapter_upload_path,
        )
        self._chapters[chapter_id] = chapter

    def chapter_exists(self, chapter_id: str, chapter_index: int) -> bool:
        if chapter_index:
            chapter_path_str = str(chapter_index) + "_" + chapter_id
        else:
            chapter_path_str = chapter_id
        chapter_path = self._chapter_path(chapter_path_str)
        if chapter_path.exists():
            logger.info(f"Chapter {chapter_path_str} already exists: {chapter_path}")
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

    def _get_chapter_data_wrapped(self, arg):
        return self._download_chapter(*arg)  # Unpacks (index, chapter_id, on_disk)

    def _download_chapter(
        self, chapter_index: int, chapter_id: str, already_on_disk: bool
    ) -> "ChapterDownload":
        """
        Download every page of one chapter and RETURN the result. Hermetic: it
        does NOT mutate ``self.manga``, does NOT write to disk, and does NOT read
        ``settings()`` -- the parent owns those.

        This is the worker run by the ``ThreadPool`` in ``_get_chapters_data``.
        Threads share the parent's address space (and its logging config -- no
        per-worker re-init needed), so this could mutate ``self.manga`` directly,
        but it deliberately stays hermetic and returns its result instead: the
        parent owns Manga assembly + writing in ``_add_download_to_manga``. That
        keeps the worker free of shared-state races AND means a CPU-bound step
        (e.g. descramble) could later be handed to a process pool unchanged.
        The parent decides ``already_on_disk`` (it owns config/disk access).

        ``pages`` is ``None`` for a chapter that was skipped -- already complete on
        disk, no page urls, or the chapter doesn't exist.
        """
        if already_on_disk:
            return ChapterDownload(chapter_id, chapter_index, pages=None, complete=True)

        self.adapter.info(
            f"Downloading chapter {chapter_index} from {self.parser.manga.chapter_url(chapter_id)}"
        )
        try:
            urls = self.parser.manga.page_urls(chapter_id)
        except ChapterDoesntExist as e:
            self.adapter.error(e)
            return ChapterDownload(
                chapter_id, chapter_index, pages=None, complete=False
            )
        if not urls:
            self.adapter.warning(f"No pages found for chapter {chapter_id}, skipping")
            return ChapterDownload(
                chapter_id, chapter_index, pages=None, complete=False
            )

        with ThreadPool() as pool:
            # download the chapter images in parallel threads
            pages_data = list(pool.map(self.parser.manga.page_data, urls))

        if not pages_data:
            self.adapter.error(f"No data for chapter {chapter_id}")
            return ChapterDownload(
                chapter_id, chapter_index, pages=None, complete=False
            )

        # flag a chapter that is missing pages (any page that didn't 'success')
        missing = [page for page in pages_data if page[2] != "success"]
        complete = not missing
        if missing:
            self.adapter.error(
                f"Chapter {chapter_id} is missing pages "
                f"{','.join(str(page[0]) for page in missing)}, "
                f"url={self.parser.manga.chapter_url(chapter_id)}"
            )

        self.adapter.info(f"Chapter {chapter_id} done")
        return ChapterDownload(
            chapter_id, chapter_index, pages=pages_data, complete=complete
        )

    def _get_chapters_data(
        self, chapter_ids: Iterable[str] = []
    ) -> List["ChapterDownload"]:
        """
        Download a list of chapters, each in its own worker thread, and return
        the per-chapter :class:`ChapterDownload` results (in input order).

        The work is I/O-bound (network), so a ``ThreadPool`` gives real
        concurrency (the GIL is released during socket I/O) without the cost and
        spawn semantics of processes: threads share the parent's logging config
        (no per-worker re-init) and its memory, and -- because the workers log in
        the same process -- the rich progress bar below can stay pinned. The
        parent decides up front which chapters are already on disk (the only
        config/disk-dependent step) and passes that in; the parent assembles the
        Manga from the returns.
        """
        assert self.manga is not None
        chapter_ids = list(chapter_ids)
        # Parent-side (has settings/disk access): which chapters are already saved?
        worker_args = [
            (index, chapter_id, self.manga.chapter_exists(chapter_id, index))
            for index, chapter_id in enumerate(chapter_ids, start=1)
        ]
        self.adapter.info("Downloading chapters data...")
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

        results: List[ChapterDownload] = []
        with ThreadPool(self.jobs) as pool:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=get_console(),
                transient=False,
            ) as progress:
                task = progress.add_task("Downloading chapters", total=len(worker_args))
                for result in pool.imap(self._get_chapter_data_wrapped, worker_args):
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

    def get_manga_chapters(
        self,
        chapter_ids: Optional[Iterable[str]] = None,
        title: Optional[str] = None,
        preferred_name: Optional[str] = None,
    ) -> Manga:
        """
        Returns a Manga object containing the requested chapters
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
        # Author (parent-side) for ComicInfo <Writer>; None for parsers/sites
        # that don't expose one. Best-effort: a failure here must not abort a
        # download, so swallow and leave author unset.
        try:
            self.manga.author = self.parser.manga.author()
        except Exception as err:
            self.adapter.debug(f"author lookup failed: {err}")
        # Find the list of chapters for that manga, in canonical chapter order
        all_chapter_ids = sort_chapter_ids(self.parser.manga.all_chapter_ids())

        if not all_chapter_ids:
            raise Exception("Empty chapters list")

        # Selection is chapter-number based (see scraper.selection): chapter_ids are
        # selector tokens like ["9-12", "28.22"], matched against the chapter
        # numbers the site offers -- not 1-based indices into the list.
        if chapter_ids is None:
            chapter_ids = list(all_chapter_ids)
        else:
            chapter_ids = select_chapters(chapter_ids, all_chapter_ids)
        self.adapter.debug(f"chapter_ids={chapter_ids}")

        # Download the chapters in parallel worker threads. Each worker returns
        # a ChapterDownload; the PARENT owns assembly + writing below (the worker
        # stays hermetic), so the in-memory Manga is correct after the run.
        downloads = self._get_chapters_data(chapter_ids)

        for download in downloads:
            self._add_download_to_manga(download)

        return self.manga

    def _add_download_to_manga(self, download: "ChapterDownload") -> None:
        """Assemble one worker result into ``self.manga`` and write it to disk.

        Parent-side: registers the chapter, populates its pages from the worker's
        RETURN value (the only thing that survives the process boundary), and
        saves it via the writer. A skipped chapter (``pages is None`` -- already
        on disk / no pages) is still listed as metadata but written nothing.
        """
        assert self.manga is not None
        chapter_id = download.chapter_id
        if not self.manga.chapters_dict.get(chapter_id):
            self.manga.add_chapter(
                chapter_id,
                chapter_index=download.chapter_index,
                complete=download.complete,
            )

        if download.pages is None:
            return

        chapter = self.manga.chapters_dict[chapter_id]
        if not chapter.pages:
            chapter.pages = list(download.pages)  # type: ignore[assignment]

        # Persist the assembled chapter (parent-side, once -- not in the worker).
        self._create_manga_dir(self.manga.name)
        if self.writer:
            self.adapter.info(f"Saving chapter {chapter_id}")
            self.writer.write(chapter)

        # If this chapter is complete, remove any stale "-incomplete" file left by
        # an earlier partial run. chapter_exists only ever checks the COMPLETE
        # name, so without this the orphaned "<...>-incomplete.<ext>" lingers
        # forever (and you can end up with both variants side by side).
        if download.complete:
            self._remove_incomplete_sibling(chapter.file_path)

    @staticmethod
    def _incomplete_path(complete_path: Path) -> Path:
        """The ``-incomplete`` filename variant of a complete chapter path.

        ``foo_chapter_1_1.pdf`` -> ``foo_chapter_1_1-incomplete.pdf`` (mirrors how
        ``Manga.add_chapter`` appends ``-incomplete`` before the extension).
        """
        return complete_path.with_name(
            f"{complete_path.stem}-incomplete{complete_path.suffix}"
        )

    def _remove_incomplete_sibling(self, complete_path: Path) -> None:
        """Delete a leftover ``-incomplete`` file for a now-complete chapter."""
        incomplete = self._incomplete_path(complete_path)
        if incomplete.exists():
            self.adapter.info(f"Removing stale incomplete file {incomplete}")
            try:
                incomplete.unlink()
            except OSError as err:  # pragma: no cover - unexpected fs error
                self.adapter.warning(f"Could not remove {incomplete}: {err}")
