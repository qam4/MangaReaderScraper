import configparser
import functools
import logging
import os
import pdb
import re
import sys
import time
from logging import Logger, LoggerAdapter
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional, Tuple, Union

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


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging once, with a ``rich`` handler.

    Single source of truth for log setup (replaces the three duplicated
    ``logging.basicConfig`` calls). ``level`` defaults to the
    ``MANGASCRAPER_LOG_LEVEL`` env var, then ``INFO`` -- so worker processes that
    don't inherit the parent's config can be used as a pool ``initializer`` with
    no args and still pick up the level the CLI chose. ``force=True`` makes it
    idempotent (safe to call again from the CLI after argparse).
    """
    if level is None:
        level = os.environ.get(LOG_LEVEL_ENV, DEFAULT_LOG_LEVEL)
    level = level.upper()

    from rich.logging import RichHandler

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
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


def settings() -> configparser.ConfigParser:
    """
    Retrieve settings file contents
    """
    config = configparser.ConfigParser()
    user_config = Path.home() / ".config" / "mangascraper.ini"
    if not user_config.exists():
        create_base_config()
    config.read(str(user_config))
    return config


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
