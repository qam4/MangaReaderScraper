import configparser
import functools
import logging
import os
import pdb
import re
import sys
import time
from contextlib import contextmanager
from logging import Logger, LoggerAdapter
from pathlib import Path
from typing import Any, Callable, Iterator, MutableMapping, Optional, Tuple, Union

import requests  # type: ignore
from requests.adapters import HTTPAdapter  # type: ignore
from urllib3.util.retry import Retry

from scraper.exceptions import CannotExtractChapter

# Env var carrying the chosen log level into spawned worker processes, which do
# NOT inherit the parent's logging config (esp. on Windows spawn). The CLI sets
# it once; each pool worker reads it back via ``configure_logging`` as its pool
# ``initializer``. Keeps logging configured in ONE place instead of three.
LOG_LEVEL_ENV = "MANGASCRAPER_LOG_LEVEL"
DEFAULT_LOG_LEVEL = "INFO"


@functools.lru_cache(maxsize=1)
def get_console() -> Any:
    """The single shared ``rich`` Console for the process.

    Both the logging ``RichHandler`` and the download ``rich.progress.Progress``
    must render through the SAME Console so rich coordinates one Live region --
    that is what keeps the progress bar pinned at the bottom while log lines
    scroll above it. Two separate Consoles (or rich's progress alongside tqdm's
    own Live) fight over the terminal and the bar gets scrolled away.
    """
    from rich.console import Console

    return Console(stderr=True)


@contextmanager
def atomic_write_path(final_path: Union[str, Path]) -> Iterator[Path]:
    """Yield a temp path to write to, then atomically move it onto ``final_path``.

    The single shared primitive for non-corrupting file output (cbz/pdf/bundle).
    Callers write their file to the yielded temp path; on clean exit it is
    ``os.replace``d onto ``final_path`` (atomic on the same filesystem, so a
    reader never sees a half-written file). On ANY exception the temp file is
    removed and ``final_path`` is left untouched -- so a failed write leaves no
    corrupt or partial artifact behind (the bug where a bare ``ZipFile`` whose
    ``close()`` was never reached left an unreadable ``.cbz``).

    The temp path is a sibling of ``final_path`` (same directory/filesystem so
    the rename is atomic, not a cross-device copy), suffixed ``.part``.
    """
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(f"{final.name}.part")
    try:
        yield tmp
    except BaseException:
        # failed (or interrupted) mid-write: drop the partial, keep final intact
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as err:  # pragma: no cover - unexpected fs error
                logging.warning(f"Could not remove partial file {tmp}: {err}")
        raise
    else:
        os.replace(tmp, final)  # atomic publish


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging once, with a ``rich`` handler.

    Single source of truth for log setup (replaces the three duplicated
    ``logging.basicConfig`` calls). ``level`` defaults to the
    ``MANGASCRAPER_LOG_LEVEL`` env var, then ``INFO`` -- so worker processes that
    don't inherit the parent's config can be used as a pool ``initializer`` with
    no args and still pick up the level the CLI chose. ``force=True`` makes it
    idempotent (safe to call again from the CLI after argparse).

    The handler renders through the shared ``get_console()`` so it shares one
    Live region with the download progress bar (keeps the bar pinned).
    """
    if level is None:
        level = os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)
    level = level.upper()

    from rich.logging import RichHandler

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(console=get_console(), rich_tracebacks=True, show_path=False)
        ],
        force=True,
    )


class CustomAdapter(LoggerAdapter):
    """
    Prepends manga name & volume to the logger message
    """

    def process(
        self, msg: str, kwargs: MutableMapping[str, Union[str, int]]
    ) -> Tuple[str, MutableMapping[str, Union[str, int]]]:
        manga = self.extra.get("manga")
        volume = self.extra.get("volume")
        if volume:
            return f"[{manga}:{volume}] {msg}", kwargs
        return f"[{manga}] {msg}", kwargs


def get_adapter(
    logger: Logger,
    manga: str,
    volume: Optional[Union[str, int]] = None,  # noqa: E251
) -> CustomAdapter:
    if volume:
        extra = {"manga": manga, "volume": volume}
    else:
        extra = {"manga": manga}
    return CustomAdapter(logger, extra)


def download_timer(func: Callable) -> Callable:
    """
    Manga volume(s) download timer
    """

    @functools.wraps(func)
    def wrapper_timer(*args) -> Any:
        """
        Assumes last arg is the volume digit
        """
        start = time.time()
        returned = func(*args)
        run_time = round(time.time() - start, 1)
        logging.info(f"Volumes downloaded in {run_time} seconds")
        return returned

    return wrapper_timer


def create_base_config() -> None:
    config = configparser.ConfigParser()
    config.add_section("config")

    user_home = Path.home()
    downloaddir = user_home / "Downloads"
    configdir = user_home / ".config"
    for path in [downloaddir, configdir]:
        path.mkdir(parents=True, exist_ok=True)

    config["config"]["manga_directory"] = str(downloaddir)
    config["config"]["manga_bundle_directory"] = str(downloaddir)
    # mangareader.net is defunct; default to mangabuddy (mangak.io), an
    # open-API source that works without a browser.
    config["config"]["source"] = "mangabuddy"
    config["config"]["filetype"] = "pdf"
    config["config"]["upload_root"] = "/"

    configfile = configdir / "mangascraper.ini"
    with configfile.open("w") as cf:
        config.write(cf)
    # the on-disk config just changed; drop any cached parse so the next
    # settings() call re-reads it.
    _read_settings.cache_clear()


@functools.lru_cache(maxsize=None)
def _read_settings(config_path: str) -> configparser.ConfigParser:
    """Parse the ini at ``config_path`` (cached by path).

    Keyed on the path (not no-args) so a patched ``Path.home()`` in tests maps to
    a distinct cache entry rather than reusing the real-home parse. Cleared by
    ``create_base_config`` when the file is (re)written.
    """
    config = configparser.ConfigParser()
    config.read(config_path)
    return config


def settings() -> configparser.ConfigParser:
    """
    Retrieve settings file contents.

    The ini is parsed once per path and cached (it's read on every volume/upload
    path build), so repeated calls don't re-hit disk. ``create_base_config``
    clears the cache when it rewrites the file.
    """
    user_config = Path.home() / ".config" / "mangascraper.ini"
    if not user_config.exists():
        create_base_config()
    return _read_settings(str(user_config))


# A polite default: cap parallelism at 4 even on many-core machines (we're being
# kind to the source), but never exceed the core count.
def _default_jobs() -> int:
    return min(4, os.cpu_count() or 1)


def resolve_jobs(cli_jobs: Optional[int] = None) -> int:
    """Resolve the download/bundle worker pool size.

    Precedence: explicit CLI value > ini ``[config] jobs`` > a CPU-aware default
    (``min(4, cpu_count)``). Always returns at least 1; an invalid ini value is
    warned about and ignored. Lower values are gentler on the source.
    """
    if cli_jobs is not None:
        return max(1, cli_jobs)
    raw = settings()["config"].get("jobs", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logging.warning("Invalid [config] jobs=%r; using default", raw)
    return _default_jobs()


def extract_chapter_number(chapter_string: str) -> str:
    """
    Extracts the chapter digit substring in a string that
    details manga chapter number
    """
    chapter_split = chapter_string.lower().split()
    if "chapter" not in chapter_split:
        raise CannotExtractChapter(
            f"Can't find chapter substring in {chapter_string.split()}"
        )
    chapter_name_index = chapter_split.index("chapter") + 1
    chapter_string = chapter_split[chapter_name_index]
    chapter_string_split = re.split(r"\D", chapter_string)
    if "" in chapter_string_split:
        chapter_string_split.remove("")
    chapter_number = chapter_string_split[0]
    if not chapter_number.isdigit():
        raise CannotExtractChapter(f"Cannot find chapter digit in {chapter_number}")
    if len(chapter_number) > 1 and chapter_number.startswith("0"):
        chapter_number = chapter_number.lstrip("0")
    return chapter_number


def request_session(max_attempts: int = 10, intervals: float = 0.2) -> requests.Session:
    """
    Requests session with custom max reattempts and time intervals

    Usage:
      req = request_session()
      req.get(<url>)
    """
    req = requests.Session()
    retries = Retry(total=max_attempts, backoff_factor=intervals)
    for protocol in ["http", "https"]:
        req.mount(f"{protocol}://", HTTPAdapter(max_retries=retries))
    return req


def menu_input(msg="", prompt=">> "):
    """
    Custom input with error handeling
    """
    try:
        text = f"\n{msg}\n\n{prompt}" if msg else f"\n{prompt}"
        choice = input(text)
        if choice.lower() in ["q", "quit"]:
            raise KeyboardInterrupt
        return choice
    except KeyboardInterrupt:
        print("\nExiting...\n")
        sys.exit()


class ForkedPdb(pdb.Pdb):
    """
    A Pdb subclass that may be used from a forked multiprocessing child
    """

    def interaction(self, *args, **kwargs) -> None:
        _stdin = sys.stdin
        try:
            sys.stdin = open("/dev/stdin")
            pdb.Pdb.interaction(self, *args, **kwargs)  # type: ignore
        finally:
            sys.stdin = _stdin
