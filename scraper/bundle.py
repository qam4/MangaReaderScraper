"""
Bundle manga into volumes with multiple chapters
"""

import logging
import os
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from itertools import repeat
from logging import LoggerAdapter
from multiprocessing.pool import ThreadPool
from typing import Dict, List, Optional, Tuple

from scraper.manga import Manga
from scraper.selection import ChapterId
from scraper.utils import (
    atomic_write_path,
    get_adapter,
    get_console,
    resolve_jobs,
    settings,
)

logger = logging.getLogger(__name__)

# Neutral fallback for the ComicInfo <Writer> tag when the manga has no known
# author. Overridable via the ini ``[config] writer`` key. NOTE: extracting the
# real per-manga author (so this fallback is rarely needed) is backlog item E1.
WRITER_DEFAULT = "Unknown"


def _configured_writer() -> str:
    """The default comic ``<Writer>``: the ini ``[config] writer`` if set, else
    the neutral ``WRITER_DEFAULT``. Never hardcodes a specific person."""
    try:
        return settings()["config"].get("writer", WRITER_DEFAULT) or WRITER_DEFAULT
    except Exception:
        return WRITER_DEFAULT


# Default kcc-c2e flags, used when the ini has no ``[config] kcc_args``.
# Everything here is a rendering choice you may legitimately want to change per
# device, which is why it's configurable -- see ``_kcc_args``.
#
#   -u / --upscale  scale pages smaller than the device resolution up to it,
#                   rather than leaving them small. NOTE this is also what
#                   pushes small pages into KCC's resize/auto-crop path at all
#                   (image.py: `elif method == BICUBIC and not upscale: pass`).
#   --hq            HQ Panel View -- the tap-to-zoom magnification regions.
#                   KCC 5.x enabled Panel View by default; 10.x disables it
#                   unless --hq or -2 is passed (comic2ebook.py: `if not
#                   options.hq and not options.autoscale: options.panelview =
#                   False`), so without this the volumes have no zoom.
#   -g 1.8          Gamma. KCC used to take this from the device profile and the
#                   default KV profile carried 1.8; in 10.x every Kindle profile
#                   carries 1.0, making gamma correction a no-op and rendering
#                   pages lighter and flatter. Caveat: an explicit gamma also
#                   applies to COLOUR pages, which the old profile-driven path
#                   deliberately skipped.
#   --metadatatitle 1
#                   Book title = "<Series> Vol. NN: <Title>", i.e. the chapter
#                   range is part of the title the Kindle shows
#                   ("Naruto Vol. 01: Chapters 700-710"). Without it KCC stops
#                   at "Naruto Vol. 01", and every volume of a series looks the
#                   same in the library list. (2 would use <Title> alone.)
#
# Deliberately NOT here: ``-o`` and the input path (we compute those), and
# ``--tempdir`` (a correctness requirement, force-injected by ``_kcc_args``).
KCC_ARGS_DEFAULT = "-u --hq -g 1.8 --metadatatitle 1"

# Required for parallel bundling; see ``Bundle._convert_to_mobi``. Injected even
# when the user overrides ``kcc_args``, because dropping it silently corrupts
# concurrent conversions rather than merely looking different.
KCC_REQUIRED_ARGS = ["--tempdir"]


# File extensions KCC treats as pages. Mirrors kindlecomicconverter.shared
# .IMAGE_TYPES (v10.2.0) instead of importing it, because kcc is an optional
# extra and bundle.py must import without it. A bookmark's page index has to
# count exactly what KCC counts, or every chapter after the first lands on the
# wrong page.
_KCC_IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".jp2", ".avif")


def _chapter_label(chapter_id: Optional[str]) -> str:
    """Kindle table-of-contents label for one chapter, from its SITE id.

    Numeric ids ("700.5") read as "Chapter 700.5". Opaque slugs (sites whose
    chapters carry no number) are shown as-is rather than inventing a number.
    Unknown -> "" (the builder drops empty bookmarks). Pure.
    """
    if not chapter_id:
        return ""
    if ChapterId(chapter_id).value is None:
        return chapter_id
    return f"Chapter {chapter_id}"


def _chapter_range(chapter_ids: List[Optional[str]]) -> str:
    """ComicInfo ``<Title>`` for a volume: the REAL chapter numbers it covers.

    "Chapters 700-710", or "Chapter 5" for a one-chapter volume. Unlike the
    filename's ``ch1-2`` -- which counts positions in this run's selection and
    is frozen by the Calibre contract -- this uses the site's chapter ids, so it
    is what the reader actually wants to see. Reaches the device title through
    ``--metadatatitle 1``, which is in ``KCC_ARGS_DEFAULT``. Pure.
    """
    ids = [cid for cid in chapter_ids if cid]
    if not ids:
        return ""
    if ids[0] == ids[-1]:
        return _chapter_label(ids[0])
    return f"Chapters {ids[0]}-{ids[-1]}"


def _comic_info_xml(
    series: str,
    volume: int,
    title: str,
    writer: str,
    bookmarks: Optional[List[Tuple[int, str]]] = None,
) -> str:
    """Build the ``ComicInfo.xml`` embedded in each volume ``.cbz``.

    This is the ONLY metadata channel to the device: KCC parses it
    (``kindlecomicconverter/metadata.py``) and turns it into the MOBI's title,
    author and table of contents. Only the fields KCC actually reads are
    emitted:

    * ``Series``  -> the book title on the Kindle. KCC uses it verbatim when no
      ``-t`` is passed, so this must be the SERIES name ("Naruto"), not the
      volume title. (It used to be given the volume title, which is why the
      device showed "Naruto vol1 ch1-2" as the book's name.)
    * ``Volume``  -> KCC appends ``" Vol. NN"`` to the title (zero-padded to 2).
    * ``Title``   -> KCC appends ``": <Title>"`` when ``--metadatatitle 1`` is
      passed, which ``KCC_ARGS_DEFAULT`` does -- so the chapter range is in the
      device title unless the user's ``kcc_args`` drops the flag.
    * ``Writer``  -> the MOBI's author (``dc:creator``).
    * ``Pages``   -> one ``<Page Image=... Bookmark=.../>`` per chapter. KCC
      collects these as bookmarks and uses them to label the NCX/nav table of
      contents, which is what makes in-volume chapter navigation readable.
      WITHOUT them KCC falls back to the archive's folder names, so the Kindle
      shows internal stems like "Naruto_chapter_701_700".

    ``Number`` is deliberately NOT emitted: KCC appends ``" #" + Number`` to the
    device title UNCONDITIONALLY (unlike ``Title``, which is opt-in), and in
    ComicInfo it means an issue number, which a multi-chapter volume does not
    have.

    Empty fields are SKIPPED, never written as empty tags. KCC's ``parseXML``
    reads every field via ``.firstChild.nodeValue``, which raises on an empty
    element, and ``getMetadata`` catches that with a bare ``except Exception``
    that deletes the file and returns -- so one empty tag would make KCC
    silently discard the whole ComicInfo, author and bookmarks included.

    ``bookmarks`` is ``[(zero_based_page_index, label), ...]``. Built with
    ElementTree rather than string interpolation so that an author or series
    containing ``&`` or ``<`` cannot produce invalid XML (which would hit the
    same silent discard) -- the old template interpolated raw text, and author
    names now come from the sites.

    Pure and unit-tested.
    """
    root = ET.Element(
        "ComicInfo",
        {
            "xmlns:xsd": "http://www.w3.org/2001/XMLSchema",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        },
    )
    for tag, value in (
        ("Series", series),
        ("Volume", str(volume)),
        ("Title", title),
        ("Writer", writer),
    ):
        if value and value.strip():
            ET.SubElement(root, tag).text = value
    bookmarks = [(i, label) for i, label in bookmarks or [] if label and label.strip()]
    if bookmarks:
        pages = ET.SubElement(root, "Pages")
        for image_index, label in bookmarks:
            ET.SubElement(pages, "Page", {"Image": str(image_index), "Bookmark": label})
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'


def _kcc_args() -> List[str]:
    """The kcc-c2e flags to pass, from the ini ``[config] kcc_args``.

    Passthrough by design: the value is split with ``shlex`` and handed to
    kcc-c2e as-is, so any KCC flag works (``-p KPW34`` for a different device
    profile, ``-s`` to stretch instead of crop, ``--colorautocontrast``, ...)
    without this module needing to know about it. KCC validates them.

    Absent key -> ``KCC_ARGS_DEFAULT``. Present but EMPTY (``kcc_args =``) ->
    no flags, for "just run plain kcc-c2e". ``KCC_REQUIRED_ARGS`` is always
    appended, de-duped if the user listed it too. Pure apart from ``settings()``.
    """
    try:
        configured = settings()["config"].get("kcc_args", None)
    except Exception:
        configured = None
    raw = KCC_ARGS_DEFAULT if configured is None else configured
    args = shlex.split(raw)
    return args + [a for a in KCC_REQUIRED_ARGS if a not in args]


def extract_cbz(archive_path, cbz_output_path):
    if os.path.exists(cbz_output_path):
        logger.debug(f"{archive_path} already unzipped")
        shutil.rmtree(cbz_output_path)

    logger.debug(f"Unzipping {archive_path} to {cbz_output_path}")
    try:
        with zipfile.ZipFile(archive_path, "r") as zip:
            zip.extractall(cbz_output_path)
    except zipfile.BadZipFile as e:
        logger.error(f"Invalid zip file {archive_path}")
        raise e
    except FileNotFoundError as e:
        logger.error(f"Zip file not found {archive_path}")
        raise e
    except Exception as e:
        logger.error("An error occurred:", e)
        raise e


def ceiling_division(n, d):
    """
    Ceiling division
    """
    return -(n // -d)


class Bundle:
    """
    Bundles the manga into volumes with multiple chapters
    Also convert to MOBI
    """

    def __init__(
        self, manga: Manga, chapters_per_volume: int, jobs: Optional[int] = None
    ) -> None:
        self.manga: Manga = manga
        self.chapters_per_volume: int = chapters_per_volume
        self.adapter: LoggerAdapter = get_adapter(logger, manga.name)
        # Prefer the extracted author (ComicInfo <Writer>); fall back to the ini
        # [config] writer or the neutral default. Never the maintainer's name.
        self.writer = manga.author or _configured_writer()
        self.jobs: int = resolve_jobs(jobs)

    def _get_manga_download_dir(self) -> str:
        """
        Get path to manga download dir.
        """
        return settings()["config"]["manga_directory"]

    def _get_manga_bundle_dir(self) -> str:
        """
        Get path to manga bundle dir.
        """
        config = settings()["config"]
        return config.get(
            "manga_bundle_directory", config.get("manga_directory", os.getcwd())
        )

    def is_obsolete(self, target: str, dependencies: List[str]) -> bool:
        """make-style check: does ``target`` need rebuilding from these inputs?

        True when the target is absent, when any input is newer than it, or when
        an input is MISSING. That last case matters: a chapter that failed to
        download is still registered in the Manga (under an ``-incomplete``
        name) but no file was ever written for it, and
        ``os.path.getmtime`` on it raised ``FileNotFoundError`` here -- from
        OUTSIDE ``create_volume``'s try block, so it escaped through
        ``bundle``'s pool and aborted every remaining volume. Reporting
        "obsolete" instead lets the build attempt fail cleanly inside the try,
        which skips just this volume and leaves the existing target intact.
        """
        if not os.path.isfile(target):
            return True
        target_mtime = os.path.getmtime(target)
        for dependency in dependencies:
            if not os.path.isfile(dependency):
                logger.warning(
                    f"Chapter file {dependency} is missing; cannot rebuild "
                    f"{os.path.basename(target)} from it"
                )
                return True
            if target_mtime - os.path.getmtime(dependency) < 0:
                return True
        return False

    def create_volume_wrapped(self, arg):
        return self.create_volume(*arg)  # Unpacks args

    def _convert_to_mobi(self, cbz_path: str, mobi_path: str) -> None:
        """Convert a volume ``.cbz`` to ``.mobi`` via kcc-c2e.

        Raises a clear RuntimeError if kcc-c2e is missing OR the conversion
        fails -- previously the result of ``subprocess.run`` was ignored, so a
        kcc-c2e failure (bad input, or its own kindlegen dependency missing)
        produced a missing/partial MOBI silently. We check the exit code AND
        that the expected output exists, surfacing kcc-c2e's output on failure.

        ``--tempdir`` makes KCC create its ``KCC-*`` work dirs next to the
        source ``.cbz`` instead of in the system temp dir. This is what lets us
        run conversions in parallel (see ``bundle``'s Pool): without it, every
        kcc-c2e process deletes *all* ``KCC-*`` dirs in the system temp at
        startup (its ``checkPre`` orphan sweep), wiping the in-progress work
        dirs of its siblings -- verified to corrupt concurrent runs. This is
        also why we no longer need the patched KCC fork; upstream's
        ``--tempdir`` solves the concurrency problem cleanly.
        """
        if shutil.which("kcc-c2e") is None:
            raise RuntimeError(
                "kcc-c2e not found on PATH -- MOBI bundling needs Kindle Comic "
                "Converter installed. Run `uv sync --extra bundle` (plus 7-Zip "
                "on PATH and kindlegen for the MOBI step). See the README "
                "'Bundling to MOBI' section."
            )
        # Rendering flags come from the ini (see _kcc_args / KCC_ARGS_DEFAULT);
        # -o and the input path are ours because they're computed per volume.
        command = [
            "kcc-c2e",
            *_kcc_args(),
            "-o",
            os.path.dirname(mobi_path),
            cbz_path,
        ]
        logger.info(f"command={command}")
        result = subprocess.run(command, capture_output=True, text=True)
        # KCC's own output (incl. its `print('ERROR: ...')` lines) goes to
        # stdout, so surface debug runs and -- on failure -- BOTH streams.
        combined = ((result.stdout or "") + (result.stderr or "")).strip()
        if combined:
            logger.debug(f"kcc-c2e output for {cbz_path}:\n{combined}")
        if result.returncode != 0 or not os.path.exists(mobi_path):
            output_tail = combined[-800:]
            raise RuntimeError(
                f"kcc-c2e failed converting {cbz_path} to MOBI "
                f"(exit {result.returncode}); expected {mobi_path}. "
                "Is kindlegen available to KCC?"
                + (f"\nkcc-c2e output:\n{output_tail}" if output_tail else "")
            )

    def create_volume(self, volume_index: int, volume_digits: int):
        """
        Create a bundled volume
        """
        start_time = time.time()

        input_root_path = self._get_manga_download_dir()
        output_root_path = self._get_manga_bundle_dir()

        manga_chapters = self.manga.chapters
        manga_title = self.manga.name
        writer = self.writer
        manga_folder = os.path.join(input_root_path, manga_title)
        output_folder = os.path.join(output_root_path, manga_title)
        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(os.path.join(output_folder, "cbz"), exist_ok=True)
        os.makedirs(os.path.join(output_folder, "mobi"), exist_ok=True)

        cbz_files = [os.path.basename(chapter.file_path) for chapter in manga_chapters]
        # The site's chapter id for each Chapter object ("700.5"). Needed for the
        # ComicInfo labels because Chapter.number is NOT the chapter number --
        # it is the stable file index (position in the full series list).
        chapter_ids: Dict[int, str] = {
            id(c): cid for cid, c in self.manga.chapters_dict.items()
        }

        # Create the volume .cbz file
        volume = volume_index + 1

        chapter_start = volume_index * self.chapters_per_volume
        chapter_end = min(
            len(cbz_files) - 1, chapter_start + self.chapters_per_volume - 1
        )
        num_chapters = chapter_end - chapter_start + 1

        series = manga_title
        if num_chapters == 1:
            title = f"{manga_title} vol{volume:0{volume_digits}} ch{chapter_start + 1}"
        else:
            title = f"{manga_title} vol{volume:0{volume_digits}} ch{chapter_start + 1}-{chapter_end + 1}"

        # The "<series> - <title>" filename is DELIBERATE and is an external
        # contract -- do NOT "simplify" the apparently-duplicated series name.
        # Calibre reads metadata from the FILENAME for .cbz -- its built-in CBZ
        # reader only parses a ComicBookInfo zip comment, never ComicInfo.xml
        # (calibre ebooks/metadata/archive.py, get_comic_metadata) -- via a
        # user-configured regex that splits on " - ". This shape is what lets it
        # extract the series and the volume title into the right columns; the
        # regex is in the README, "Importing volumes into Calibre". Collapsing it
        # to just "<title>.cbz" breaks that import silently -- the books still
        # load, with the wrong fields. Pinned by
        # test_create_volume_filename_is_the_calibre_contract.
        # (MOBI is unaffected either way: there Calibre reads the real embedded
        # metadata that KCC writes from our ComicInfo.xml.)
        volume_cbz_path = os.path.join(output_folder, "cbz", f"{series} - {title}.cbz")

        # check if the cbz archive needs an update
        dependencies = [
            str(manga_chapters[chapter_start + chapter].file_path)
            for chapter in range(num_chapters)
        ]
        if self.is_obsolete(volume_cbz_path, dependencies):
            logger.info(f"Creating {volume_cbz_path}...")
            # Write to a temp sibling and atomically publish: if anything below
            # raises (bad chapter file, extract failure, interrupt), the partial
            # is removed and no corrupt .cbz is left at volume_cbz_path. (Was a
            # bare ZipFile whose z.close() was skipped on error -> unreadable cbz.)
            try:
                with atomic_write_path(volume_cbz_path) as tmp_cbz:
                    with zipfile.ZipFile(tmp_cbz, "w") as z:
                        # Chapter bookmarks for the Kindle table of contents:
                        # (index of the chapter's first page in the volume,
                        # label). Collected while zipping because the index is
                        # the running page count, so ComicInfo.xml is written
                        # LAST, below.
                        bookmarks: List[Tuple[int, str]] = []
                        pages_so_far = 0

                        chapter = 0
                        while chapter < num_chapters:
                            cbz_file = cbz_files[chapter_start + chapter]
                            file_root, _ = os.path.splitext(cbz_file)
                            logger.debug(f"file_root={file_root}")

                            archive_path = os.path.join(manga_folder, cbz_file)
                            logger.debug(f"archive_path={archive_path}")
                            folder = os.path.join(manga_folder, file_root)
                            logger.debug(f"folder={folder}")
                            # let an extract failure propagate: the atomic
                            # context drops the partial cbz rather than publish
                            # a half-built volume.
                            extract_cbz(archive_path, folder)

                            # Add every file in the current folder to the volume
                            chapter_pages = 0
                            for root, _dirs, files in os.walk(folder):
                                for filename in files:
                                    cbz_input_path = os.path.join(folder, filename)
                                    cbz_output_path = os.path.join(file_root, filename)
                                    z.write(cbz_input_path, cbz_output_path)
                                    if filename.lower().endswith(_KCC_IMAGE_TYPES):
                                        chapter_pages += 1

                            # remove the unzipped chapter
                            shutil.rmtree(folder)

                            # A zero-page chapter gets no bookmark: its index
                            # would point past the end of KCC's page list when
                            # it is the last chapter, and KCC indexes that list
                            # unchecked (IndexError, failed conversion).
                            if chapter_pages:
                                bookmarks.append(
                                    (
                                        pages_so_far,
                                        _chapter_label(
                                            chapter_ids.get(
                                                id(
                                                    manga_chapters[
                                                        chapter_start + chapter
                                                    ]
                                                )
                                            )
                                        ),
                                    )
                                )
                            pages_so_far += chapter_pages

                            chapter += 1

                        z.writestr(
                            "ComicInfo.xml",
                            _comic_info_xml(
                                series=series,
                                volume=volume,
                                title=_chapter_range(
                                    [
                                        chapter_ids.get(id(c))
                                        for c in manga_chapters[
                                            chapter_start : chapter_end + 1
                                        ]
                                    ]
                                ),
                                writer=writer,
                                bookmarks=bookmarks,
                            ),
                        )
            except Exception:
                # extracting/zipping a chapter failed; the partial cbz was
                # cleaned up by atomic_write_path. Abort this volume.
                logger.error(f"Failed to build {volume_cbz_path}; skipping volume")
                return

        # Convert the .cbz to .mobi
        volume_mobi_path = volume_cbz_path.replace("cbz", "mobi")
        # check if the mobi file needs an update
        if self.is_obsolete(volume_mobi_path, [volume_cbz_path]):
            logger.info(f"Creating {volume_mobi_path}...")
            self._convert_to_mobi(volume_cbz_path, volume_mobi_path)

        elapsed_time = time.time() - start_time
        time_str = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
        logger.debug(f"{title} creation duration: {time_str}")

    def bundle(self):
        manga_chapters = self.manga.chapters
        num_volumes = ceiling_division(len(manga_chapters), self.chapters_per_volume)
        volume_digits = len(str(num_volumes))

        logger.info(f"Bundling {num_volumes} volumes...")
        # rich.progress.Progress sharing the logging Console (see
        # utils.get_console) keeps this bar pinned at the bottom while logs
        # scroll above -- one coordinated rich Live region. This works because
        # the pool below is a ThreadPool: the workers log in THIS process, so
        # rich's single-process Live can coordinate them (a process Pool's
        # workers logged to a separate stderr and the bar couldn't pin).
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        # ThreadPool, not a process Pool: each volume's heavy work is an EXTERNAL
        # kcc-c2e subprocess (see _convert_to_mobi), and subprocess.run releases
        # the GIL while it waits -- so the conversions still run in true parallel
        # as separate OS processes, while the supervising Python threads just
        # block. Threads also inherit the parent's logging config (no
        # initializer) and let the bar pin. The only GIL-bound step is the .cbz
        # zip, which is light (manga images are already compressed).
        with ThreadPool(self.jobs) as pool:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=get_console(),
                transient=False,
            ) as progress:
                task = progress.add_task("Bundling volumes", total=num_volumes)
                for _ in pool.imap(
                    self.create_volume_wrapped,
                    zip(range(num_volumes), repeat(volume_digits)),
                ):
                    progress.advance(task)
