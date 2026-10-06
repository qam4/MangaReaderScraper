"""
Manga building blocks & factories
"""

import json
import logging
import re
from dataclasses import dataclass, field
from multiprocessing.pool import ThreadPool
from pathlib import Path
from typing import Dict, Generator, Iterable, List, Optional

from scraper.exceptions import (
    # PageDoesNotExist,
    # ChapterAlreadyExists,
    ChapterAlreadyPresent,
    ChapterDoesntExist,
    OfflineSeriesNotFound,
    PageAlreadyPresent,
)
from scraper.new_types import PageData
from scraper.parsers.types import SiteParser
from scraper.selection import ChapterId, select_chapters, sort_chapter_ids
from scraper.utils import (
    atomic_write_path,
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


@dataclass
class DownloadSummary:
    """End-of-run tally of a download, derived from the worker results.

    Categories (from each ``ChapterDownload``'s ``complete`` + ``pages``):
      * ``downloaded``       -- fetched fully this run (complete, has pages)
      * ``already_present``  -- skipped because already on disk (complete, no
        pages returned)
      * ``incomplete``       -- fetched but missing some pages (saved with an
        ``-incomplete`` suffix)
      * ``failed``           -- couldn't fetch at all (no page urls / chapter
        doesn't exist)
    """

    downloaded: List[str]
    already_present: List[str]
    incomplete: List[str]
    failed: List[str]

    @property
    def requested(self) -> int:
        return (
            len(self.downloaded)
            + len(self.already_present)
            + len(self.incomplete)
            + len(self.failed)
        )

    @property
    def ok(self) -> bool:
        """True when nothing is incomplete or failed."""
        return not self.incomplete and not self.failed


def summarize_downloads(downloads: Iterable["ChapterDownload"]) -> DownloadSummary:
    """Tally worker results into a :class:`DownloadSummary`. Pure / unit-tested.

    A ``pages is None`` result is either an already-on-disk skip (complete) or a
    hard failure (not complete); a result WITH pages is a full download
    (complete) or a missing-pages one (not complete). Each id list is sorted in
    canonical chapter order for stable, readable output.
    """
    downloaded: List[str] = []
    already_present: List[str] = []
    incomplete: List[str] = []
    failed: List[str] = []
    for d in downloads:
        if d.pages is None:
            (already_present if d.complete else failed).append(d.chapter_id)
        else:
            (downloaded if d.complete else incomplete).append(d.chapter_id)
    return DownloadSummary(
        downloaded=sort_chapter_ids(downloaded),
        already_present=sort_chapter_ids(already_present),
        incomplete=sort_chapter_ids(incomplete),
        failed=sort_chapter_ids(failed),
    )


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

    # the site's chapter id ("700.5"), which is also what names the file
    number: str
    file_path: Path
    upload_path: Path
    # position in the site's full chapter list this run: reading order only,
    # never part of the filename (see Manga.add_chapter)
    order: Optional[int] = None
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
        """Chapters in reading order.

        That is the site's order (``Chapter.order``) when known -- the only
        correct order for ids that don't sort as numbers, like opaque slugs --
        and numeric chapter-id order for chapters added without one.
        """

        def key(c: Chapter) -> tuple:
            # (0, n) sorts before (1, id), and the second elements are only
            # ever compared against their own kind
            if c.order is not None:
                return (0, c.order)
            return (1, ChapterId(c.number))

        return sorted(self._chapters.values(), key=key)

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
        """Register a chapter.

        The file is named after the chapter's SITE ID alone --
        ``<name>_chapter_<id>[-incomplete].<ext>`` -- because the id is the one
        thing about a chapter that doesn't change. Its position in the site's
        list (``chapter_index``) used to be part of the name, so inserting or
        removing one chapter re-downloaded every chapter after it under a new
        name and left the old copies behind. The position is still kept, as
        ``Chapter.order``, but only in memory, to put chapters in reading order.
        """
        if self.chapters_dict.get(chapter_id):
            raise ChapterAlreadyPresent(f"Chapter {chapter_id} is already present")

        chapter_path_str = chapter_id if complete else f"{chapter_id}-incomplete"
        chapter = Chapter(
            number=chapter_id,
            file_path=self._chapter_path(chapter_path_str),
            upload_path=self._chapter_upload_path(chapter_path_str),
            order=chapter_index,
        )
        self._chapters[chapter_id] = chapter

    def chapter_exists(self, chapter_id: str) -> bool:
        """Is this chapter already saved, complete? (An ``-incomplete`` file
        doesn't count: those are always retried.)"""
        chapter_path = self._chapter_path(chapter_id)
        if chapter_path.exists():
            logger.info(f"Chapter {chapter_id} already exists: {chapter_path}")
            return True
        return False

    def migrate_position_named_files(self, known_ids: Iterable[str]) -> None:
        """Rename chapter files from the old position-based scheme to the id one.

        Old: ``<name>_chapter_<position>_<id>[-incomplete].<ext>``.
        New: ``<name>_chapter_<id>[-incomplete].<ext>``.

        Without this, every chapter downloaded before the change would be
        fetched again. Per chapter id: the newest complete file is renamed into
        place (an incomplete one only when there is no complete one), and any
        other old copy -- such as the duplicates the position bug produced -- is
        MOVED into ``.superseded/`` in the same folder. Nothing is deleted.

        Only ids the site currently lists are touched, and a file whose whole
        tail is itself a known id is taken to be new-style already, so a file
        is never renamed on a guess. Rename keeps the mtime, so volumes built
        from these chapters aren't spuriously considered stale.
        """
        known = set(known_ids)
        folder = self._chapter_path("x").parent
        if not folder.is_dir():
            return
        pattern = re.compile(
            rf"^{re.escape(self.name)}_chapter_"
            r"(?P<position>\d+)_(?P<id>.+?)(?P<incomplete>-incomplete)?"
            r"\.(?P<ext>cbz|pdf)$"
        )
        found: Dict[tuple, List[tuple]] = {}
        for path in folder.iterdir():
            match = pattern.match(path.name) if path.is_file() else None
            if not match:
                continue
            chapter_id = match["id"]
            tail = f"{match['position']}_{chapter_id}"
            if chapter_id not in known or tail in known:
                continue
            key = (chapter_id, match["ext"])
            found.setdefault(key, []).append((path, bool(match["incomplete"])))

        for (chapter_id, ext), files in found.items():
            files.sort(key=lambda f: f[0].stat().st_mtime, reverse=True)
            stem = f"{self.name}_chapter_{chapter_id}"
            complete_target = folder / f"{stem}.{ext}"
            incomplete_target = folder / f"{stem}-incomplete.{ext}"
            keep: Optional[tuple] = None
            if not complete_target.exists():
                completes = [f for f in files if not f[1]]
                if completes:
                    keep = (completes[0][0], complete_target)
                elif not incomplete_target.exists():
                    keep = (files[0][0], incomplete_target)
            for path, _ in files:
                if keep and path == keep[0]:
                    path.rename(keep[1])
                    logger.info(f"Renamed {path.name} -> {keep[1].name}")
                else:
                    moved = _set_aside(path)
                    logger.warning(
                        f"Moved duplicate chapter file {path.name} to {moved}"
                    )


# What a download records beside its chapters so bundling can later run
# without the site: series name, source, url, author and the FULL chapter list
# in the site's order. Written by MangaBuilder, read by load_offline_manga.
SERIES_FILE = ".series.json"


def load_offline_manga(
    name: str, filetype: str = "cbz", chapter_ids: Optional[Iterable[str]] = None
) -> Manga:
    """Rebuild a series' ``Manga`` from local files only, for ``--offline``.

    Uses the ``SERIES_FILE`` a previous download wrote and the chapter files on
    disk. The result matches what an online run would build -- same chapters,
    order, file paths and author -- so it produces the same volumes, and finds
    them current when nothing changed. That includes a chapter whose file is
    missing: it is registered as incomplete, exactly as an online run registers
    a chapter that failed to download, so volume boundaries don't shift.

    ``chapter_ids`` are ``--chapters`` selector tokens, applied to the recorded
    list. Raises ``OfflineSeriesNotFound`` when there is no record.
    """
    name = sanitize_filename(name)
    manga = Manga(name, filetype)
    record_path = manga._chapter_path("x").parent / SERIES_FILE
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise OfflineSeriesNotFound(
            f"No offline record for '{name}' ({record_path} is missing). Run the "
            "same download once without --offline to create it; --manga must "
            "be the series folder name under manga_directory."
        )
    except ValueError as err:
        raise OfflineSeriesNotFound(f"Unreadable offline record {record_path}: {err}")

    all_ids = [str(c) for c in record.get("chapters") or []]
    if not all_ids:
        raise OfflineSeriesNotFound(f"Offline record {record_path} lists no chapters")
    manga.author = record.get("author")
    manga.migrate_position_named_files(all_ids)

    selected = all_ids if chapter_ids is None else select_chapters(chapter_ids, all_ids)
    order = {cid: position for position, cid in enumerate(all_ids, start=1)}
    missing = []
    for chapter_id in selected:
        complete = manga._chapter_path(chapter_id).exists()
        if not complete:
            missing.append(chapter_id)
        manga.add_chapter(
            chapter_id, chapter_index=order[chapter_id], complete=complete
        )
    if missing:
        logger.warning(
            f"[{name}] No complete file for chapters {', '.join(missing)}; "
            "volumes containing them are skipped unless an -incomplete file exists"
        )
    return manga


def _loose_name(name: str) -> str:
    """ "Dragon Ball Super" and "dragon-ball-super" -> "dragonballsuper"."""
    return "".join(c for c in name.lower() if c.isalnum())


def _set_aside(path: Path) -> Path:
    """Move ``path`` into ``.superseded/`` beside it, never overwriting."""
    target_dir = path.parent / ".superseded"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / path.name
    counter = 1
    while target.exists():
        target = target_dir / f"{path.stem}.{counter}{path.suffix}"
        counter += 1
    path.rename(target)
    return target


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
        # {chapter_id: 1-based position in the FULL series list}. Set in
        # get_manga_chapters; becomes each Chapter's reading ``order``. It is
        # NOT part of any filename (see Manga.add_chapter).
        self._chapter_order: Dict[str, int] = {}

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
            f"Downloading chapter {chapter_id} from {self.parser.manga.chapter_url(chapter_id)}"
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
        # Parent-side (has settings/disk access): which chapters are already
        # saved? Files are named by chapter id alone, so the check matches across
        # runs whatever else was selected and however the site's list has
        # shifted. The index passed along is the chapter's reading order -- its
        # position in the full series list, falling back to the selection
        # position only if a chapter somehow isn't in the map.
        worker_args = [
            (index, chapter_id, self.manga.chapter_exists(chapter_id))
            for chapter_id, index in (
                (cid, self._chapter_order.get(cid, pos))
                for pos, cid in enumerate(chapter_ids, start=1)
            )
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
                # imap_UNORDERED so the bar advances as each chapter actually
                # finishes, not in submission order: a slow first chapter (e.g.
                # waiting on the shared browser / a manual Cloudflare solve) would
                # otherwise hold back the bar while later chapters already
                # completed. Order doesn't matter -- each ChapterDownload is
                # self-describing and the parent assembles by chapter id.
                for result in pool.imap_unordered(
                    self._get_chapter_data_wrapped, worker_args
                ):
                    results.append(result)
                    progress.advance(task)
        return results

    def _write_series_record(self, all_chapter_ids: List[str]) -> None:
        """Save ``SERIES_FILE`` beside the chapters (see ``load_offline_manga``).

        Best effort: failing to write it must not fail the download, so an
        error is a warning. Written atomically, so a crash can't leave a torn
        record that a later offline run would misread.
        """
        assert self.manga is not None
        record = {
            "format": 1,
            "name": self.manga.name,
            "source": getattr(self.parser, "source_name", None),
            "manga_url": self.parser.manga.manga_url,
            "author": self.manga.author,
            "chapters": list(all_chapter_ids),
        }
        path = self.manga._chapter_path("x").parent / SERIES_FILE
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with atomic_write_path(path) as tmp:
                tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        except OSError as err:
            self.adapter.warning(f"Could not record {path}: {err}")

    def _existing_series_folder(self, name: str) -> str:
        """The folder name to use for this series: an existing folder whose
        ``SERIES_FILE`` records the same source and url, else ``name``.

        Without this the folder depended on HOW you asked: a search names it
        after the result's title ("Dragon Ball Super"), ``--manga`` after the
        slug ("dragon-ball-super"). Same series, two folders, and the second
        run downloaded everything again without a word.

        Folders from before records existed can't be matched for sure. If one
        merely LOOKS like this series (same letters and digits, ignoring case
        and punctuation) and we're about to create a new folder, that is only
        warned about, with the ``--override_name`` that would reuse it --
        adopting a folder on a guess could merge two different series.
        """
        root = Path(settings()["config"]["manga_directory"])
        if not root.is_dir():
            return name
        source = getattr(self.parser, "source_name", None)
        url = self.parser.manga.manga_url
        matches: List[str] = []
        lookalikes: List[str] = []
        for folder in root.iterdir():
            if not folder.is_dir():
                continue
            record_path = folder / SERIES_FILE
            if record_path.is_file():
                try:
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if record.get("manga_url") == url and record.get("source") == source:
                    matches.append(folder.name)
            elif folder.name != name and _loose_name(folder.name) == _loose_name(name):
                lookalikes.append(folder.name)

        if matches:
            chosen = name if name in matches else sorted(matches)[0]
            if chosen != name:
                self.adapter.info(
                    f"Using existing folder '{chosen}' for this series (its "
                    f"{SERIES_FILE} records the same source and url)"
                )
            return chosen
        if lookalikes and not (root / name).exists():
            self.adapter.warning(
                f"Folder(s) {', '.join(repr(f) for f in sorted(lookalikes))} look like "
                f"this series but have no {SERIES_FILE}, so they're not reused and "
                f"chapters will be saved under '{name}'. To reuse one, run with "
                f"--override_name {sorted(lookalikes)[0]!r}"
            )
        return name

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
        explicit_name = bool(preferred_name)
        preferred_name = (
            preferred_name
            if preferred_name
            else title
            if title
            else self.parser.manga.manga_url
        )
        preferred_name = sanitize_filename(preferred_name)
        if not explicit_name:
            # An explicit --override_name is the user's choice and is kept.
            preferred_name = self._existing_series_folder(preferred_name)
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

        # Each chapter's position in the full series list: its reading order.
        self._chapter_order = {
            chapter_id: index
            for index, chapter_id in enumerate(all_chapter_ids, start=1)
        }
        # Files from before chapters were named by id are renamed in place, so
        # they're found as already downloaded instead of being fetched again.
        self.manga.migrate_position_named_files(all_chapter_ids)
        # Record what a later --offline bundle needs (the full list, not just
        # this selection), before any download can fail.
        self._write_series_record(all_chapter_ids)

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

        self._log_download_summary(downloads)
        return self.manga

    def _log_download_summary(self, downloads: List["ChapterDownload"]) -> None:
        """Print an end-of-run tally so the user sees, at a glance, how the
        download went -- and is told explicitly about any incomplete/failed
        chapters (which are easy to miss in the scrolled-past per-chapter logs).
        """
        s = summarize_downloads(downloads)
        self.adapter.info(
            f"Download summary: {s.requested} requested -> "
            f"{len(s.downloaded)} downloaded, "
            f"{len(s.already_present)} already present, "
            f"{len(s.incomplete)} incomplete, {len(s.failed)} failed"
        )
        if s.incomplete:
            self.adapter.warning(
                f"Incomplete chapters (missing pages): {', '.join(s.incomplete)}"
            )
        if s.failed:
            self.adapter.warning(
                f"Failed chapters (could not fetch): {', '.join(s.failed)}"
            )

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
