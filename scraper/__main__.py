import argparse
import logging
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
from scraper.utils import configure_logging, menu_input, settings

CONFIG = settings()["config"]

logger = logging.getLogger(__name__)

# Configure logging once at import with the default level; cli() re-applies the
# user's --log-level (and propagates it to worker processes via the env var).
configure_logging()


def get_chapter_values(chapter: str) -> List[str]:
    """
    Split a ``--chapters`` argument into selector tokens.

    Ranges (``9-12``) are passed through verbatim as single tokens; they are
    resolved against the manga's actual chapter list later by
    ``scraper.selection.select_chapters`` (chapter-number based), so that ranges
    can span decimal chapters and tolerate gaps. Comma-separated values are
    split into individual tokens.
    """
    return [token for token in chapter.split(",") if token]


def normalize_chapters(chapters: Optional[List[str]]) -> Optional[List[str]]:
    """
    Flatten raw ``--chapters`` tokens into selector tokens, or None if empty.
    """
    if not chapters:
        return None
    flattened: List[str] = []
    for chapter in chapters:
        flattened += get_chapter_values(chapter)
    return flattened


def manga_search(
    query: List[str],
    parser: SiteParserClass,
    preselected: Optional[List[str]] = None,
) -> Tuple[str, str, List[str]]:
    """
    Search for a manga, let the user pick one, and return its name + the chapter
    selection.

    The user always picks WHICH manga from the results. For the chapters: if
    ``preselected`` is given (a CLI ``--chapters`` the caller already parsed), it
    is used as-is and the interactive chapter prompt is skipped -- so
    ``--search ... --chapters 1-3`` is honored instead of silently ignored.
    Otherwise we prompt (Enter alone = all chapters).
    """
    menu = SearchMenu(query, parser)
    manga = menu.handle_options()
    logger.debug(f"[manga_search] manga={manga}")
    title = manga["title"]
    url = manga["manga_url"]
    if preselected:
        logger.debug(
            f"[manga_search] using CLI --chapters {preselected}, skipping prompt"
        )
        return (title, url, list(preselected))
    msg = (
        "Which chapter(s) do you want to download "
        "(Enter alone to download all chapters)?"
    )
    chapters = menu_input(msg)
    logger.debug(f"[manga_search] chapters={chapters}")
    return (title, url, chapters.split())


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
    manga_title: Optional[str],
    chapters: Optional[List[str]],
    filetype: str,
    parser: SiteParserClass,
    preferred_name: Optional[str] = None,
    jobs: Optional[int] = None,
) -> Manga:
    """Download a manga"""
    downloader = Download(manga_url, filetype, parser, jobs=jobs)
    manga = downloader.download_chapters(chapters, manga_title, preferred_name)
    return manga


def upload(manga: Manga, service: str) -> Uploader:
    # Imported lazily so the optional `upload` extra (dropbox/pcloud) is only
    # required when a user actually uploads. Keeps `import scraper.__main__`
    # (and thus the whole test suite) working without those packages installed.
    from scraper.uploaders.uploaders import DropboxUploader, PcloudUploader

    services: Dict[str, Type[Uploader]] = {
        "dropbox": DropboxUploader,
        "pcloud": PcloudUploader,
    }
    uploader = services[service]()
    return uploader(manga)


def bundle(manga: Manga, chapter_per_volume: int, jobs: Optional[int] = None):
    bundle = Bundle(manga, chapter_per_volume, jobs=jobs)
    return bundle.bundle()


def cli(arguments: List[str]) -> dict:
    logger.debug(f"arguments={arguments}")
    parser = get_parser()
    args = vars(parser.parse_args(arguments))
    logger.debug(f"args={args}")
    # Apply the chosen log level once. Workers share this process now
    # (ThreadPool), so they inherit the config -- no env propagation needed.
    log_level = args.get("log_level") or "INFO"
    configure_logging(log_level)
    manga_parser = get_manga_parser(args["source"])
    title = None
    # Did the user originally search? If so, the slug came from a result they
    # picked -- a later MangaDoesNotExist is a fetch/parse failure, not a wrong
    # slug, so we must NOT fall back to another (identical) search.
    user_searched = bool(args["search"])

    if args["remove"] and not args["upload"]:
        raise IOError("Cannot use --remove without --upload")

    if args["search"]:
        # Honor an explicit CLI --chapters (otherwise it'd be silently dropped by
        # the interactive prompt); the user still picks WHICH manga.
        title, args["manga"], args["chapters"] = manga_search(
            args["search"], manga_parser, preselected=args.get("chapters")
        )

    elif args["manga"]:
        args["manga"] = " ".join(args["manga"])
    else:
        raise IOError("Missing argument --manga or --search")

    args["chapters"] = normalize_chapters(args["chapters"])

    if args["bundle"]:
        args["filetype"] = "cbz"

    logger.debug(f"[download_manga] args={args}")
    try:
        manga = download_manga(
            manga_url=args["manga"],
            manga_title=title,
            chapters=args["chapters"],
            filetype=args["filetype"],
            parser=manga_parser,
            preferred_name=args["override_name"],
            jobs=args.get("jobs"),
        )
    except MangaDoesNotExist:
        # The direct slug lookup failed. Fall back to a search for the same
        # term -- as a plain branch, not by re-serializing argv and re-entering
        # cli(). The user picks a result, then we download that.
        if user_searched:
            # Already came from a search the user picked from: re-searching
            # would just repeat the same failed fetch. Let cli_entry report it.
            raise
        logging.warning(
            f"No manga found for {args['manga']}. Searching for closest match."
        )
        args["search"] = [args["manga"]]
        # Carry the CLI --chapters (already normalized above) through the
        # fallback search so it's honored there too.
        title, args["manga"], args["chapters"] = manga_search(
            args["search"], manga_parser, preselected=args["chapters"]
        )
        args["chapters"] = normalize_chapters(args["chapters"])
        manga = download_manga(
            manga_url=args["manga"],
            manga_title=title,
            chapters=args["chapters"],
            filetype=args["filetype"],
            parser=manga_parser,
            preferred_name=args["override_name"],
            jobs=args.get("jobs"),
        )

    if args["upload"]:
        upload(manga, args["upload"])

    if args["remove"]:
        for chapter in manga.chapters:
            chapter.file_path.unlink()

    if args["bundle"]:
        bundle(manga, args["bundle"], jobs=args.get("jobs"))

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
    except MangaDoesNotExist as err:
        # The chosen manga's page/chapter-list couldn't be loaded or parsed
        # (unreachable, or the site blocked automated access). Report cleanly
        # rather than dumping a traceback.
        logging.error(
            f"Could not load '{err}': the page was unreachable or its chapter "
            "list couldn't be parsed (the site may be blocking automated access)."
        )
        sys.exit(1)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="downloads and converts manga to pdf or cbz format"
    )
    parser.add_argument("--manga", "-m", type=str, help="manga series name", nargs="*")
    parser.add_argument(
        "--search", "-s", type=str, help="search manga reader", nargs="*"
    )
    parser.add_argument(
        "--chapters",
        "-q",
        nargs="+",
        type=str,
        help="chapter(s) to download",
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
        choices={"dropbox", "pcloud"},
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
        help="delete downloaded chapters aftering uploading to a cloud service",
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
        "--jobs",
        "-j",
        type=int,
        default=None,
        help="number of parallel download/bundle workers (lower is gentler on "
        "the source; default: ini [config] jobs, else min(4, CPU count))",
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
