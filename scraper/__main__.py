import argparse
import logging
import os
import sys
from typing import Dict, List, Optional, Tuple, Type

from scraper.bundle import Bundle
from scraper.download import Download
from scraper.exceptions import MangaDoesNotExist, NoSearchResultsFound
from scraper.manga import Manga
from scraper.menu import SearchMenu
from scraper.parsers.types import SiteParserClass
from scraper.registry import available_sources, get_source
from scraper.uploaders.types import Uploader
from scraper.utils import LOG_LEVEL_ENV, configure_logging, menu_input, settings

CONFIG = settings()["config"]

logger = logging.getLogger(__name__)

# Configure logging once at import with the default level; cli() re-applies the
# user's --log-level (and propagates it to worker processes via the env var).
configure_logging()


def get_volume_values(volume: str) -> List[str]:
    """
    Split a ``--volumes`` argument into selector tokens.

    Ranges (``9-12``) are passed through verbatim as single tokens; they are
    resolved against the manga's actual chapter list later by
    ``scraper.selection.select_chapters`` (chapter-number based), so that ranges
    can span decimal chapters and tolerate gaps. Comma-separated values are
    split into individual tokens.
    """
    return [token for token in volume.split(",") if token]


def normalize_volumes(volumes: Optional[List[str]]) -> Optional[List[str]]:
    """
    Flatten raw ``--volumes`` tokens into selector tokens, or None if empty.
    """
    if not volumes:
        return None
    flattened: List[str] = []
    for vol in volumes:
        flattened += get_volume_values(vol)
    return flattened


def manga_search(
    query: List[str], parser: SiteParserClass
) -> Tuple[str, str, List[str]]:
    """
    Search for a manga and return the manga name and volumes
    selected by user input
    """
    menu = SearchMenu(query, parser)
    manga = menu.handle_options()
    logger.debug(f"[manga_search] manga={manga}")
    title = manga["title"]
    url = manga["manga_url"]
    msg = (
        "Which volume(s) do you want to download (Enter alone to download all volumes)?"
    )
    volumes = menu_input(msg)
    logger.debug(f"[manga_search] volumes={volumes}")
    return (title, url, volumes.split())


def get_manga_parser(source: str) -> SiteParserClass:
    """
    Use the string to return correct parser class, looked up in the registry.
    """
    parser = get_source(source)
    if not parser:
        available = ", ".join(available_sources())
        raise ValueError(f"{source} is not supported try {available}")
    return parser


def download_manga(
    manga_url: str,
    manga_title: str,
    volumes: Optional[List[str]],
    filetype: str,
    parser: SiteParserClass,
    preferred_name: Optional[str] = None,
) -> Manga:
    """Download a manga"""
    downloader = Download(manga_url, filetype, parser)
    manga = downloader.download_volumes(volumes, manga_title, preferred_name)
    return manga


def upload(manga: Manga, service: str) -> Uploader:
    # Imported lazily so the optional `upload` extra (dropbox/pcloud) is only
    # required when a user actually uploads. Keeps `import scraper.__main__`
    # (and thus the whole test suite) working without those packages installed.
    from scraper.uploaders.uploaders import DropboxUploader, PcloudUploader

    services: Dict[str, Type[Uploader]] = {
        "dropbox": DropboxUploader,
        # "mega": MegaUploader,
        "pcloud": PcloudUploader,
    }
    uploader = services[service]()
    return uploader(manga)


def bundle(manga: Manga, chapter_per_volume: int):
    bundle = Bundle(manga, chapter_per_volume)
    return bundle.bundle()


def cli(arguments: List[str]) -> dict:
    logger.debug(f"arguments={arguments}")
    parser = get_parser()
    args = vars(parser.parse_args(arguments))
    logger.debug(f"args={args}")
    # Apply the chosen log level once, and propagate it to spawned worker
    # processes (which don't inherit logging config) via the env var that
    # configure_logging reads as its pool initializer.
    log_level = args.get("log_level") or "INFO"
    os.environ[LOG_LEVEL_ENV] = log_level
    configure_logging(log_level)
    manga_parser = get_manga_parser(args["source"])
    title = None

    if args["remove"] and not args["upload"]:
        raise IOError("Cannot use --remove without --upload")

    if args["search"]:
        title, args["manga"], args["volumes"] = manga_search(
            args["search"], manga_parser
        )

    elif args["manga"]:
        args["manga"] = " ".join(args["manga"])
    else:
        raise IOError("Missing argument --manga or --search")

    args["volumes"] = normalize_volumes(args["volumes"])

    if args["bundle"]:
        args["filetype"] = "cbz"

    logger.debug(f"[download_manga] args={args}")
    try:
        manga = download_manga(
            manga_url=args["manga"],
            manga_title=title,
            volumes=args["volumes"],
            filetype=args["filetype"],
            parser=manga_parser,
            preferred_name=args["override_name"],
        )
    except MangaDoesNotExist:
        # The direct slug lookup failed. Fall back to a search for the same
        # term -- as a plain branch, not by re-serializing argv and re-entering
        # cli(). The user picks a result, then we download that.
        logging.warning(
            f"No manga found for {args['manga']}. Searching for closest match."
        )
        args["search"] = [args["manga"]]
        title, args["manga"], args["volumes"] = manga_search(
            args["search"], manga_parser
        )
        args["volumes"] = normalize_volumes(args["volumes"])
        manga = download_manga(
            manga_url=args["manga"],
            manga_title=title,
            volumes=args["volumes"],
            filetype=args["filetype"],
            parser=manga_parser,
            preferred_name=args["override_name"],
        )

    if args["upload"]:
        upload(manga, args["upload"])

    if args["remove"]:
        for volume in manga.volumes:
            volume.file_path.unlink()

    if args["bundle"]:
        bundle(manga, args["bundle"])

    return args


def cli_entry() -> None:
    """
    Required as entry_point in setup.py cannot take args,
    however, we need cli() to take args for unit testing
    purposes. Hence the need for this function.
    """
    try:
        cli(sys.argv[1:])
    except NoSearchResultsFound as err:
        # The parser layer no longer calls sys.exit(); the CLI owns process
        # termination. Report the empty result and exit cleanly.
        logging.error(str(err))
        sys.exit(1)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="downloads and converts manga volumes to pdf or cbz format"
    )
    parser.add_argument("--manga", "-m", type=str, help="manga series name", nargs="*")
    parser.add_argument(
        "--search", "-s", type=str, help="search manga reader", nargs="*"
    )
    parser.add_argument(
        "--volumes", "-q", nargs="+", type=str, help="manga volume to download"
    )
    parser.add_argument("--output", "-o", default=CONFIG["manga_directory"])
    parser.add_argument(
        "--filetype",
        "-f",
        type=str,
        choices={"pdf", "cbz"},
        default=CONFIG["filetype"],
        help="format to store manga as",
    )
    parser.add_argument(
        "--source",
        "-z",
        type=str,
        choices=available_sources(),
        default=CONFIG["source"],
        help="website to scrape data from",
    )
    parser.add_argument(
        "--upload",
        "-u",
        type=str,
        choices={"dropbox", "mega", "pcloud"},
        help="upload manga to a cloud storage service",
    )
    parser.add_argument(
        "--override_name",
        "-n",
        type=str,
        help="change manga name for all saved/uploaded files",
    )
    parser.add_argument(
        "--remove",
        "-r",
        action="store_true",
        help="delete downloaded volumes aftering uploading to a cloud service",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="v0.50",
        help="display the installed version number of the application",
    )
    parser.add_argument(
        "--bundle",
        type=int,
        help="Specify the number of chapters per volume in the output manga",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="logging verbosity (default: INFO)",
    )
    return parser


if __name__ == "__main__":
    cli_entry()
