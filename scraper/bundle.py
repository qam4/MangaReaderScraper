"""
Bundle manga into volumes with multiple chapters
"""

import functools
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import threading
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


# Version of what create_volume puts in a volume (ComicInfo fields, archive
# layout). Written into ComicInfo <Notes>, which KCC ignores, so it travels
# inside the artifact: bump it whenever the volume format changes and every
# existing volume compares as stale and is rebuilt on the next --bundle,
# instead of users having to know to delete them.
#   2 = Series/Volume/Title + chapter bookmarks.
#   3 = chapter folders inside a volume are prefixed with their position
#       ("003_<stem>", see _volume_folder), and chapter files are named by id.
BUNDLE_FORMAT = 3

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
        # ignored by KCC; it is the in-artifact format stamp (BUNDLE_FORMAT)
        ("Notes", f"MangaReaderScraper bundle format {BUNDLE_FORMAT}"),
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


# Per-series record of HOW each MOBI was built, so a change of kcc-c2e flags or
# of the KCC version rebuilds it (file times alone can't see that). Lives in the
# series' bundle folder, beside cbz/ and mobi/ rather than inside mobi/, so
# copying the mobi/ folder to a Kindle carries no clutter.
MOBI_STAMPS_FILE = ".mobi-stamps.json"
# Volumes convert on a ThreadPool and all record into the one stamps file.
_STAMPS_LOCK = threading.Lock()


@functools.lru_cache(maxsize=1)
def _kcc_version() -> str:
    """Installed KCC version, for the MOBI stamp; "unknown" if it can't be read.

    Read from package metadata, so it reflects the KCC in THIS environment (the
    `bundle` extra). A kcc-c2e on PATH from some other install isn't seen, so a
    version change there would go unnoticed -- the args part of the stamp still
    works either way.
    """
    try:
        from importlib.metadata import version

        return version("KindleComicConverter")
    except Exception:
        return "unknown"


def _mobi_stamp() -> Dict[str, object]:
    """What a MOBI built right now would be built with."""
    return {"kcc_args": _kcc_args(), "kcc_version": _kcc_version()}


def _read_stamps(path: str) -> Dict[str, object]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record_stamp(path: str, mobi_name: str, stamp: Dict[str, object]) -> None:
    """Record one MOBI's stamp. Read-modify-write under a lock (volumes convert
    in parallel threads) and published atomically (no torn JSON on a crash)."""
    with _STAMPS_LOCK:
        stamps = _read_stamps(path)
        stamps[mobi_name] = stamp
        with atomic_write_path(path) as tmp:
            tmp.write_text(
                json.dumps(stamps, indent=2, sort_keys=True), encoding="utf-8"
            )


def _volume_folder(position: int, count: int, chapter_path: str) -> str:
    """Folder a chapter's pages go into inside a volume: ``"003_<stem>"``.

    KCC reads a volume's pages in natural-sort order of these folder names,
    and the bookmark page indices assume that order is OUR reading order. The
    chapter file stem alone can't promise that once files are named by chapter
    id: opaque slugs sort alphabetically ("alpha-main" before "zeta-intro"
    whatever the site's order), and natural sort reads "700.10" as after
    "700.9". A zero-padded position prefix makes the two orders identical by
    construction. Pure.
    """
    width = max(3, len(str(count)))
    stem = os.path.splitext(os.path.basename(chapter_path))[0]
    return f"{position:0{width}d}_{stem}"


def _chapter_members(chapter_path: str) -> List[str]:
    """Entry names (files only) of a chapter archive, in archive order."""
    with zipfile.ZipFile(chapter_path) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def _page_count(names: List[str]) -> int:
    """How many of these entries KCC will treat as pages."""
    return sum(1 for n in names if n.lower().endswith(_KCC_IMAGE_TYPES))


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

    def _volume_is_stale(
        self, cbz_path: str, dependencies: List[str], comic_info: str
    ) -> bool:
        """Does the volume ``.cbz`` need rebuilding?

        Yes when ``is_obsolete`` says so (missing, or a chapter file is newer /
        missing), AND ALSO when the ComicInfo it would get differs from the one
        it has. File times alone can't see that second case, and it is the one
        that matters after a scraper update (new ComicInfo fields, or a
        ``BUNDLE_FORMAT`` bump), or when the site's author changes: the volume
        is newer than every chapter, so it used to be kept, silently.

        An existing file that can't be read as a volume is rebuilt too.
        """
        if self.is_obsolete(cbz_path, dependencies):
            return True
        name = os.path.basename(cbz_path)
        try:
            with zipfile.ZipFile(cbz_path) as z:
                existing = z.read("ComicInfo.xml").decode("utf-8")
        except (OSError, KeyError, UnicodeDecodeError, zipfile.BadZipFile):
            logger.info(f"{name}: existing file is not a readable volume; rebuilding")
            return True
        if existing != comic_info:
            logger.info(f"{name}: metadata or volume format changed; rebuilding")
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
        # KCC is pointed at an EMPTY per-volume folder, never at mobi/ itself.
        # KCC never overwrites: when "<stem>.mobi" already exists it writes
        # "<stem>_kcc0.mobi" beside it (comic2ebook.py getOutputFilename), so a
        # rebuild into mobi/ left the stale book in place, and checking for
        # mobi_path then passed on that STALE file -- a silent "success"
        # (reproduced with the real kcc-c2e + kindlegen). Converting into a
        # fresh folder means KCC always produces exactly "<stem>.mobi", which
        # we then os.replace onto the target: atomic on the same filesystem,
        # and the previous MOBI survives untouched if the conversion fails.
        mobi_dir = os.path.dirname(mobi_path) or "."
        stem = os.path.splitext(os.path.basename(cbz_path))[0]
        work_dir = tempfile.mkdtemp(prefix=".kcc-", dir=mobi_dir)
        try:
            # Rendering flags come from the ini (see _kcc_args /
            # KCC_ARGS_DEFAULT); -o and the input path are ours.
            command = ["kcc-c2e", *_kcc_args(), "-o", work_dir, cbz_path]
            logger.info(f"command={command}")
            result = subprocess.run(command, capture_output=True, text=True)
            # KCC's own output (incl. its `print('ERROR: ...')` lines) goes to
            # stdout, so surface debug runs and -- on failure -- BOTH streams.
            combined = ((result.stdout or "") + (result.stderr or "")).strip()
            if combined:
                logger.debug(f"kcc-c2e output for {cbz_path}:\n{combined}")
            produced = os.path.join(work_dir, f"{stem}.mobi")
            if result.returncode != 0 or not os.path.isfile(produced):
                output_tail = combined[-800:]
                found = sorted(os.listdir(work_dir))
                raise RuntimeError(
                    f"kcc-c2e failed converting {cbz_path} to MOBI "
                    f"(exit {result.returncode}); expected {stem}.mobi"
                    + (f", got {found}" if found else "")
                    + ". Is kindlegen available to KCC?"
                    + (f"\nkcc-c2e output:\n{output_tail}" if output_tail else "")
                )
            os.replace(produced, mobi_path)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def create_volume(self, volume_index: int, volume_digits: int):
        """
        Create a bundled volume
        """
        start_time = time.time()

        output_root_path = self._get_manga_bundle_dir()

        manga_chapters = self.manga.chapters
        manga_title = self.manga.name
        writer = self.writer
        output_folder = os.path.join(output_root_path, manga_title)
        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(os.path.join(output_folder, "cbz"), exist_ok=True)
        os.makedirs(os.path.join(output_folder, "mobi"), exist_ok=True)

        # Create the volume .cbz file
        volume = volume_index + 1

        chapter_start = volume_index * self.chapters_per_volume
        chapter_end = min(
            len(manga_chapters) - 1, chapter_start + self.chapters_per_volume - 1
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

        members = manga_chapters[chapter_start : chapter_end + 1]
        dependencies = [str(c.file_path) for c in members]

        # Plan the volume BEFORE deciding whether to rebuild it: the ComicInfo it
        # would get is part of what "up to date" means (see _volume_is_stale).
        # Each chapter's entries come straight from its archive, and the SAME
        # list is both counted for the bookmarks and copied into the volume, so
        # the page indices can't drift from what's written.
        try:
            entries = [_chapter_members(path) for path in dependencies]
        except Exception as err:
            # missing / unreadable chapter file (e.g. a chapter that failed to
            # download): skip just this volume, keep any existing one intact
            logger.error(f"Failed to build {volume_cbz_path} ({err}); skipping volume")
            return

        bookmarks: List[Tuple[int, str]] = []
        pages_so_far = 0
        for chapter, names in zip(members, entries):
            pages = _page_count(names)
            # A zero-page chapter gets no bookmark: its index would point past
            # the end of KCC's page list when it is the last chapter, and KCC
            # indexes that list unchecked (IndexError, failed conversion).
            if pages:
                bookmarks.append((pages_so_far, _chapter_label(chapter.number)))
            pages_so_far += pages
        comic_info = _comic_info_xml(
            series=series,
            volume=volume,
            title=_chapter_range([c.number for c in members]),
            writer=writer,
            bookmarks=bookmarks,
        )

        if self._volume_is_stale(volume_cbz_path, dependencies, comic_info):
            logger.info(f"Creating {volume_cbz_path}...")
            # Write to a temp sibling and atomically publish: if anything below
            # raises (bad chapter file, interrupt), the partial is removed and no
            # corrupt .cbz is left at volume_cbz_path.
            try:
                with atomic_write_path(volume_cbz_path) as tmp_cbz:
                    with zipfile.ZipFile(tmp_cbz, "w") as z:
                        for position, (path, names) in enumerate(
                            zip(dependencies, entries), start=1
                        ):
                            folder = _volume_folder(position, len(members), path)
                            with zipfile.ZipFile(path) as src:
                                for name in names:
                                    z.writestr(f"{folder}/{name}", src.read(name))
                        z.writestr("ComicInfo.xml", comic_info)
            except Exception as err:
                logger.error(
                    f"Failed to build {volume_cbz_path} ({err}); skipping volume"
                )
                return

        # Convert the .cbz to .mobi
        volume_mobi_path = volume_cbz_path.replace("cbz", "mobi")
        stamps_path = os.path.join(output_folder, MOBI_STAMPS_FILE)
        stamp = _mobi_stamp()
        mobi_name = os.path.basename(volume_mobi_path)
        recorded = _read_stamps(stamps_path).get(mobi_name)
        if self.is_obsolete(volume_mobi_path, [volume_cbz_path]) or recorded != stamp:
            if recorded is not None and recorded != stamp:
                logger.info(
                    f"{mobi_name}: kcc-c2e flags or version changed; rebuilding"
                )
            logger.info(f"Creating {volume_mobi_path}...")
            self._convert_to_mobi(volume_cbz_path, volume_mobi_path)
            _record_stamp(stamps_path, mobi_name, stamp)

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
